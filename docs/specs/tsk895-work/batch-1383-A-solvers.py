# -*- coding: utf-8 -*-
"""
tsk-895 batch 1383-A: honest simulators + example self-checks.
Run: python solvers.py
"""

def to_base(n, b):
    """decimal -> digit string in base b, no leading zeros. n>=0."""
    if n == 0:
        return "0"
    digs = []
    while n > 0:
        digs.append(n % b)
        n //= b
    digs.reverse()
    return "".join(str(d) if d < 10 else chr(ord('A') + d - 10) for d in digs)

def from_base(s, b):
    return int(s, b)

# ---------------------------------------------------------------- 2292
def R_2292(N):
    b = to_base(N, 2)
    c1 = b.count('1')
    c0 = b.count('0')
    s = to_base(c1, 2) + to_base(c0, 2)
    return int(s, 2)

assert R_2292(17) == 11, R_2292(17)

def solve_2292():
    target = 214
    s = to_base(target, 2)  # binary string of 214
    best = None
    for k in range(1, len(s)):
        first, second = s[:k], s[k:]
        if first[0] != '1':
            continue
        if not (second == '0' or second[0] == '1'):
            continue
        c1 = int(first, 2)
        c0 = int(second, 2)
        if c1 < 1:
            continue
        # minimal binary N with exactly c1 ones, c0 zeros, leading 1, length c1+c0
        cand_bits = '1' + '0' * c0 + '1' * (c1 - 1)
        candN = int(cand_bits, 2)
        if R_2292(candN) == target:
            if best is None or candN < best:
                best = candN
    return best

# ---------------------------------------------------------------- 2157
from sympy import isprime

def pairs_2157(N, count_mode):
    d = str(N)
    assert len(d) == 3
    cnt = 0
    vals = set()
    for i in range(3):
        for j in range(3):
            if i == j:
                continue
            if d[i] == '0':
                continue
            v = int(d[i] + d[j])
            if isprime(v):
                cnt += 1
                vals.add(v)
    return cnt if count_mode == 'multi' else len(vals)

def solve_2157():
    for mode in ('multi', 'distinct'):
        best_cnt = -1
        best_N = None
        for N in range(100, 1000):
            c = pairs_2157(N, mode)
            if c > best_cnt or (c == best_cnt and N > best_N):
                if c >= best_cnt:
                    if c > best_cnt:
                        best_cnt = c
                        best_N = N
                    else:
                        best_N = max(best_N, N)
        print(f"  2157 mode={mode}: max_count={best_cnt}, largest N={best_N}")
    # return using 'multi' as primary (will compare both in report)

# ---------------------------------------------------------------- 3665
def R_3665(N):
    b = to_base(N, 5)
    if len(b) % 2 == 0:
        mid = len(b) // 2
        b = b[:mid] + '0' + b[mid:]
    return int(b)  # read as decimal

assert R_3665(112) == 422, R_3665(112)
assert R_3665(24) == 404, R_3665(24)

def solve_3665():
    best = None
    for N in range(1, 5000):
        if R_3665(N) <= 250:
            best = N
    return best

# ---------------------------------------------------------------- 3680
def R_3680(N):
    d = str(N)
    assert len(d) == 3 and len(set(d)) == 3
    mid = sorted(d)[1]
    full = mid + d + mid
    num = int(full)
    s = sum(int(c) for c in full)
    if num % s == 0:
        return num // s
    return None

# check example N=398 -> R=2333
r = R_3680(398)
assert r == 2333, r

def solve_3680():
    cnt_N = 0
    distinct_R = set()
    for N in range(100, 1000):
        d = str(N)
        if len(set(d)) != 3:
            continue
        r = R_3680(N)
        if r is not None:
            cnt_N += 1
            distinct_R.add(r)
    return cnt_N, len(distinct_R)

# ---------------------------------------------------------------- 3643
def R_3643(N):
    b = to_base(N, 2)
    if N % 4 == 0:
        b2 = b + b[-2:]
    else:
        rem = N % 4
        b2 = b + to_base(rem, 2)
    if b2[-1] == '0':
        b2 = b2[:-1]
    return int(b2, 2)

assert R_3643(10) == 21, R_3643(10)
assert R_3643(12) == 24, R_3643(12)

def solve_3643():
    best = None
    N = 1
    # search until we find enough margin above 213
    results = []
    for N in range(1, 20000):
        r = R_3643(N)
        if r > 213:
            results.append(r)
    return min(results)

# ---------------------------------------------------------------- 3645
def diff_3645(N):
    d = sorted(str(N))
    # max two-digit: largest digit first then next largest, but can't start with 0 (won't matter, digits>=3 for hundreds)
    # need max and min 2-digit numbers built by choosing 2 of the 3 digits (positions distinct), not starting with 0
    digs = str(N)
    perms = []
    for i in range(3):
        for j in range(3):
            if i == j:
                continue
            if digs[i] == '0':
                continue
            perms.append(int(digs[i] + digs[j]))
    return max(perms) - min(perms)

# check example N=351 -> 40
assert diff_3645(351) == 40, diff_3645(351)

def solve_3645():
    cnt = 0
    for N in range(300, 401):
        if diff_3645(N) == 20:
            cnt += 1
    return cnt

# ---------------------------------------------------------------- 3646
def R_3646_str(N):
    b = to_base(N, 5)
    s = sum(int(c) for c in b)
    if s % 2 == 1:
        b2 = b[-1] + b[:-1]
    else:
        last_dec_digit = N % 10
        tripled = last_dec_digit * 3
        b2 = b + to_base(tripled, 5)
    return b2

# check N=13 -> 32_5 ; N=14 -> 2422_5
assert R_3646_str(13) == '32', R_3646_str(13)
assert R_3646_str(14) == '2422', R_3646_str(14)

def solve_3646():
    for N in range(1, 100000):
        r = R_3646_str(N)
        if r.count('0') > 2:
            return N
    return None

# ---------------------------------------------------------------- 3647
def R_3647(N):
    b = to_base(N, 2)
    if N % 2 == 1:
        b2 = b[:-2] + '10'
    else:
        b2 = '10' + b[2:] + '1'
    return int(b2, 2)

assert R_3647(4) == 9, R_3647(4)
assert R_3647(5) == 6, R_3647(5)

def solve_3647():
    best = None
    for N in range(26, 26 + 2000):
        r = R_3647(N)
        if best is None or r < best:
            best = r
    return best

# ---------------------------------------------------------------- 3650
def R_3650(N):
    b = to_base(N, 5)
    if N % 25 == 0:
        last3 = b[-3:] if len(b) >= 3 else b.rjust(3, '0')
        b2 = last3 + b
    else:
        rem = N % 25
        b2 = b + to_base(rem, 5)
    return int(b2, 5)

assert R_3650(25) == 3150, R_3650(25)
assert R_3650(26) == 131, R_3650(26)

def solve_3650():
    for N in range(1, 200000):
        if R_3650(N) > 10000:
            return N
    return None

# ---------------------------------------------------------------- 3652
def R_3652(N):
    b = to_base(N, 3)
    if N % 3 == 0:
        b2 = ''.join(c * 2 for c in b)
    else:
        table = {'0': '1', '1': '2', '2': '0'}
        replaced = ''.join(table[c] for c in b)
        b2 = ''.join(c * 2 for c in replaced)
    return int(b2, 3)

assert R_3652(3) == 36, R_3652(3)
assert R_3652(5) == 72, R_3652(5)

def solve_3652():
    for N in range(1, 100000):
        if R_3652(N) > 120:
            return N
    return None

# ---------------------------------------------------------------- 3653
def solve_3653():
    vals = set(N // 4 for N in range(20, 601))
    return len(vals)

# ---------------------------------------------------------------- 3655
def R_3655(N):
    b = to_base(N, 2)
    if N % 2 == 1:
        b2 = '1' + b[:-2] + '10'
    else:
        b2 = b + '1'
        b2 = '10' + b2[2:]
    return int(b2, 2)

assert R_3655(6) == 9, R_3655(6)
assert R_3655(5) == 14, R_3655(5)

def solve_3655():
    best = None
    for N in range(33, 33 + 2000):
        r = R_3655(N)
        if best is None or r < best:
            best = r
    return best

# ---------------------------------------------------------------- 3658
def R_3658(N):
    b = to_base(N, 2)
    rev = b[::-1]
    res = rev + rev[-1]
    return int(res, 2)

assert R_3658(11) == 27, R_3658(11)

def solve_3658():
    for N in range(1, 100000):
        if R_3658(N) > 99:
            return N
    return None

# ---------------------------------------------------------------- 3659
def R_3659(N):
    b = to_base(N, 2)
    if N % 3 == 0:
        last3 = b[-3:] if len(b) >= 3 else b.rjust(3, '0')
        b2 = b + last3
    else:
        rem = N % 3
        val = rem * 3
        b2 = b + to_base(val, 2)
    return int(b2, 2)

assert R_3659(12) == 100, R_3659(12)
assert R_3659(4) == 19, R_3659(4)

def solve_3659():
    best_r = None
    best_n = None
    for N in range(1, 200000):
        r = R_3659(N)
        if r > 151:
            if best_r is None or r < best_r:
                best_r = r
                best_n = N
    return best_r, best_n

# ---------------------------------------------------------------- 3662
def R_3662(N):
    b = to_base(N, 3)
    c2 = b.count('2')
    b = b + to_base(c2, 3)
    c1 = b.count('1')
    b = b + to_base(c1, 3)
    c0 = b.count('0')
    b = b + to_base(c0, 3)
    return int(b, 3)

assert R_3662(5) == 150, R_3662(5)

def solve_3662():
    best = None
    for N in range(1, 100000):
        if R_3662(N) < 1000:
            best = N
    return best

# ---------------------------------------------------------------- 3663
def R_3663(N):
    b = to_base(N, 6)
    if b.count('0') >= 2:
        b = b.replace('0', '5')
    first = b[0]
    if int(first) % 2 == 0:
        rem = N % 6
        b2 = to_base(rem, 6) + b
    else:
        prod = 1
        for c in b:
            prod *= int(c)
        b2 = b + to_base(prod, 6)
    return int(b2, 6)

assert R_3663(14) == 86, R_3663(14)
assert R_3663(36) == 2581, R_3663(36)

def solve_3663():
    best = None
    for N in range(1, 5000):
        if R_3663(N) < 109:
            if best is None or N > best:
                best = N
    return best

# ---------------------------------------------------------------- 3728
def R_3728(N):
    b = to_base(N, 8)
    s = sum(int(c) for c in b)
    if s % 2 == 0:
        b2 = b + to_base(s, 8)
    else:
        b2 = to_base(s, 8) + b
    return int(b2, 8)

assert R_3728(15) == 968, R_3728(15)
assert R_3728(17) == 209, R_3728(17)

def solve_3728():
    best = None
    for N in range(483, 483 + 20000):
        r = R_3728(N)
        if best is None or r < best:
            best = r
    return best

# ---------------------------------------------------------------- 3718
def checksum_3718(A, B):
    Ah, At, Au = A // 100, (A // 10) % 10, A % 10
    Bh, Bt, Bu = B // 100, (B // 10) % 10, B % 10
    Sh = Ah + Bh
    St = At + Bt
    Su = Au + Bu
    s1 = str(Sh) + str(St)
    s2 = str(Su) + s1
    num = int(s2)
    th = (num // 1000) % 10
    hu = (num // 100) % 10
    te = (num // 10) % 10
    return f"{th}{hu}{te}"

# check example A=473 B=934 -> "131"
assert checksum_3718(473, 934) == "131", checksum_3718(473, 934)

def solve_3718():
    for A in range(999, 99, -1):
        for B in range(100, 1000):
            if checksum_3718(A, B) == "002":
                return A, B
    return None

# ---------------------------------------------------------------- 3670
def R_3670(N):
    d = [int(c) for c in str(N)]
    even_sum = sum(x for x in d if x % 2 == 0)
    A = even_sum ** 2
    B = (max(d) - min(d)) ** 3
    lo, hi = min(A, B), max(A, B)
    return int(str(lo) + str(hi))

assert R_3670(2615) == 64125, R_3670(2615)

def solve_3670():
    best = None
    for N in range(1000, 10000):
        if R_3670(N) == 4343:
            if best is None or N < best:
                best = N
    return best

# ---------------------------------------------------------------- 3714
def R_3714(N):
    b = to_base(N, 5)
    if N % 2 == 0:
        s = int(b[-1]) + int(b[-2])
        b2 = b + to_base(s, 5)
    else:
        b2 = b[-1] + b
    return int(b2, 5)

assert R_3714(316) == 1584, R_3714(316)
assert R_3714(317) == 1567, R_3714(317)

def solve_3714():
    for N in range(101, 200000):
        if R_3714(N) > 1000:
            return N
    return None

# ---------------------------------------------------------------- 3671
def check_3671(N):
    d = str(N)
    if '0' in d:
        return False
    digs = [int(c) for c in d]
    if any(N % x != 0 for x in digs):
        return False
    rev = int(d[::-1])
    revdigs = [int(c) for c in str(rev)]
    if len(str(rev)) != 3:
        return False  # would introduce leading-zero shortening; digits have no 0 anyway so rev always 3-digit
    if any(rev % x != 0 for x in revdigs):
        return False
    return True

assert check_3671(216) == True

def solve_3671():
    return sum(1 for N in range(100, 1000) if check_3671(N))

# ---------------------------------------------------------------- 3673
def R_3673(N):
    h = to_base(N, 16)
    inc = ''.join(c if c == 'F' else to_base(int(c, 16) + 1, 16) for c in h)
    rev = inc[::-1]
    dec = int(rev, 16)
    return sum(int(c) for c in str(dec))

assert R_3673(134) == 4, R_3673(134)

def solve_3673():
    for N in range(100, 1000):
        if R_3673(N) == 12:
            return N
    return None

# ---------------------------------------------------------------- 3675
def R_3675(N):
    b = to_base(N, 3)
    s = sum(int(c) for c in b)
    if s % 2 == 0:
        b2 = b + '0'
        b2 = '2' + b2[2:]
    else:
        b2 = b + '1'
        b2 = '20' + b2[2:]
    return int(b2, 3)

assert R_3675(7) == 19, R_3675(7)
assert R_3675(8) == 6, R_3675(8)

def solve_3675():
    best_R = None
    best_N = None
    for N in range(1, 100000):
        r = R_3675(N)
        if r > 75:
            if best_R is None or r < best_R or (r == best_R and N < best_N):
                if best_R is None or r < best_R:
                    best_R = r
                    best_N = N
                elif r == best_R:
                    best_N = min(best_N, N)
    return best_N, best_R

if __name__ == "__main__":
    print("2292:", solve_2292())
    solve_2157()
    print("3665:", solve_3665())
    print("3680:", solve_3680())
    print("3643:", solve_3643())
    print("3645:", solve_3645())
    print("3646:", solve_3646())
    print("3647:", solve_3647())
    print("3650:", solve_3650())
    print("3652:", solve_3652())
    print("3653:", solve_3653())
    print("3655:", solve_3655())
    print("3658:", solve_3658())
    print("3659:", solve_3659())
    print("3662:", solve_3662())
    print("3663:", solve_3663())
    print("3728:", solve_3728())
    print("3718:", solve_3718())
    print("3670:", solve_3670())
    print("3714:", solve_3714())
    print("3671:", solve_3671())
    print("3673:", solve_3673())
    print("3675:", solve_3675())
    print("ALL EXAMPLE ASSERTS PASSED")
