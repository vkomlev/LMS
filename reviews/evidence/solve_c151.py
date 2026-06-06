# -*- coding: utf-8 -*-
"""Вычисление ответов для вводных заданий курса 151 (задание 24 ЕГЭ)."""
import sys, io
from collections import Counter
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

a = ['Y', 'Z', 'X', 'Y', 'X', 'X', 'Y', 'Y', 'Z', 'X', 'X', 'X', 'X', 'Z', 'X', 'Z', 'Y', 'X', 'X', 'Z', 'X', 'X', 'X', 'Z', 'Y', 'X', 'Y', 'X', 'Z', 'Y', 'X', 'Z', 'X', 'X', 'Z', 'Z', 'Y', 'Z', 'X', 'Y', 'Z', 'Y', 'Z', 'Z', 'Z', 'Y', 'X', 'Y', 'X', 'Y', 'Z', 'X', 'X', 'Y', 'Y', 'Z', 'Z', 'Z', 'Y', 'X', 'X', 'Y', 'Z', 'X', 'Z', 'Z', 'X', 'Y', 'Y', 'Y', 'X', 'Y', 'X', 'Z', 'X', 'Y', 'Z', 'Y', 'X', 'Y', 'Y', 'Y', 'Z', 'Z', 'Z', 'X', 'X', 'X', 'Y', 'Y', 'X', 'X', 'Z', 'Y', 'Y', 'X', 'Z', 'Z', 'Y', 'X', 'Z', 'X', 'Y', 'Z', 'Y', 'Y', 'Y', 'Y', 'Y', 'Y', 'Y', 'Z', 'X', 'X', 'Z', 'Z', 'Y', 'X', 'X', 'X', 'Z', 'Y', 'Y', 'Z', 'Z', 'Y', 'Y', 'Z', 'X', 'Y', 'X', 'Z', 'Z', 'Z', 'X', 'Z', 'Y', 'Y', 'Z', 'Y', 'X', 'X', 'Y', 'Y', 'X', 'Z', 'Y', 'Y', 'X', 'Z', 'Y', 'X', 'Z', 'Z', 'X', 'X', 'Z', 'Z', 'X', 'X', 'X', 'Z', 'Y', 'Z', 'Y', 'Y', 'X', 'Z', 'Z', 'Z', 'X', 'X', 'Y', 'X', 'Z', 'Y', 'X', 'Y', 'Z', 'Z', 'X', 'X', 'X', 'Y', 'Y', 'Y', 'Z', 'Y', 'X', 'Z', 'Z', 'Y', 'X', 'X', 'Z', 'Z', 'Y', 'Y', 'Y', 'X', 'X', 'Z', 'Y', 'Z', 'Z', 'Z', 'Z', 'X', 'X', 'Y', 'Z', 'Z', 'X', 'Z', 'Y', 'Y', 'X', 'X', 'Z', 'Y', 'Y', 'Z', 'Y', 'Y', 'Y', 'Z', 'Y', 'Z', 'X', 'Y', 'Y', 'X', 'Y', 'Z', 'X', 'Y', 'X', 'Y', 'Y', 'Y', 'Y', 'Z', 'X', 'Y', 'Z', 'Z', 'Y', 'X', 'X', 'Z', 'Y', 'Z', 'X', 'Z', 'X', 'X', 'Y', 'X', 'Y', 'Y', 'X', 'X', 'Y', 'Y', 'Z', 'Z', 'Z', 'Y', 'Y', 'Z', 'X', 'X', 'Y', 'Y', 'Y', 'Z', 'Z', 'X', 'Y', 'Z', 'X', 'Y', 'Z', 'Z', 'Y', 'Z', 'Y', 'Y', 'Z', 'Y', 'X', 'X', 'Y', 'Y', 'Z', 'Z', 'Z', 'X', 'X', 'X']

b = ['X', 'A', 'U', 'D', 'I', 'B', 'R', 'O', 'V', 'W', 'G', 'N', 'N', 'R', 'S', 'G', 'A', 'O', 'X', 'O', 'R', 'E', 'S', 'J', 'O', 'Q', 'K', 'W', 'S', 'I', 'S', 'J', 'O', 'M', 'K', 'B', 'W', 'E', 'U', 'A', 'A', 'S', 'X', 'P', 'O', 'C', 'C', 'I', 'O', 'Q', 'J', 'L', 'G', 'Q', 'M', 'E', 'X', 'N', 'U', 'B', 'N', 'I', 'T', 'N', 'L', 'R', 'D', 'S', 'G', 'C', 'V', 'M', 'P', 'V', 'N', 'N', 'H', 'X', 'W', 'T', 'V', 'R', 'E', 'E', 'G', 'D', 'V', 'B', 'W', 'G', 'W', 'E', 'I', 'H', 'V', 'B', 'T', 'B', 'J', 'L', 'Y', 'D', 'D', 'K', 'Z', 'A', 'E', 'H', 'O', 'R', 'P', 'U', 'U', 'K', 'U', 'Y', 'L', 'W', 'B', 'B', 'X', 'H', 'G', 'X', 'X', 'N', 'M', 'C', 'I', 'L', 'N', 'J', 'T', 'K', 'W', 'H', 'J', 'Z', 'B', 'A', 'Y', 'P', 'F', 'F', 'C', 'M', 'M', 'T', 'R', 'Z', 'J', 'V', 'E', 'J', 'C', 'V', 'O', 'Z', 'N', 'B', 'U', 'R', 'I', 'A', 'J', 'H', 'X', 'I', 'R', 'H', 'K', 'M', 'H', 'H', 'M', 'T', 'Q', 'N', 'W', 'M', 'W', 'T', 'I', 'S', 'P', 'V', 'A', 'K', 'J', 'O', 'Z', 'Y', 'O', 'S', 'N', 'Q', 'K', 'Y', 'T', 'X', 'D', 'O', 'M', 'F', 'M', 'B', 'C', 'F', 'W', 'T', 'V', 'I', 'H', 'Z', 'T', 'L', 'I', 'W', 'K', 'Z', 'J', 'T', 'D', 'F', 'Z', 'I', 'O', 'J', 'K', 'N', 'U', 'F', 'R', 'Q', 'W', 'P', 'M', 'S', 'N', 'D', 'H', 'M', 'S', 'R', 'Z', 'F', 'P', 'J', 'U', 'K', 'C', 'D', 'F', 'O', 'E', 'P', 'J', 'K', 'U', 'Z', 'X', 'U', 'S', 'F', 'T', 'W', 'M', 'V', 'M', 'K', 'O', 'F', 'S', 'G', 'T', 'V', 'F', 'E', 'Y', 'W', 'C', 'D', 'D', 'Z', 'M', 'A', 'O', 'K', 'A', 'S', 'A', 'O', 'Y', 'N', 'S', 'K', 'U', 'P', 'U', 'S', 'I', 'B', 'X', 'K', 'X', 'L', 'L', 'C', 'L', 'L', 'Y', 'Y', 'E', 'K', 'D', 'M', 'U', 'D', 'T', 'S', 'Q', 'R', 'U', 'D', 'K', 'S', 'K', 'W', 'N', 'K', 'G', 'R', 'I', 'P', 'D', 'E', 'I', 'I', 'P', 'W', 'G', 'O', 'H', 'P', 'O', 'V', 'S', 'B', 'I', 'D', 'O', 'V', 'F', 'H', 'E', 'B', 'O', 'L', 'C', 'Z', 'I', 'I', 'N', 'Q', 'O', 'Q', 'R', 'J', 'L', 'D', 'E', 'K', 'N', 'O', 'G', 'U', 'X', 'T', 'E', 'E', 'P', 'L', 'H', 'N', 'M', 'N', 'K', 'D', 'K', 'H', 'T', 'X', 'M', 'J', 'J', 'U', 'S', 'X', 'T', 'Y', 'O', 'Z', 'V', 'E', 'O', 'E', 'I', 'Q', 'K', 'J', 'J', 'L', 'Q', 'V', 'M', 'X', 'J', 'Q', 'M', 'X', 'I', 'O', 'T', 'M', 'X', 'Q', 'Y', 'A', 'H', 'S', 'E', 'T', 'D', 'F', 'O', 'V', 'E', 'I', 'N', 'F', 'C', 'U', 'E', 'M', 'D', 'J', 'I', 'X', 'I', 'S', 'A', 'G', 'Z', 'W', 'H', 'A', 'A', 'V', 'L', 'T', 'X', 'N', 'R', 'I', 'H', 'B', 'U', 'E', 'R', 'O', 'F', 'M', 'L', 'U', 'N', 'D', 'P', 'N', 'C', 'C', 'J', 'B', 'P', 'D', 'R', 'K', 'W', 'K', 'S', 'L', 'I', 'Q', 'D', 'O', 'M', 'Z', 'P', 'K', 'A', 'R']

print(f"len(a)={len(a)}, len(b)={len(b)}")

# 1. Макс подряд Z в a
max_z = cur_z = 0
for c in a:
    if c == 'Z': cur_z += 1
    else: cur_z = 0
    max_z = max(max_z, cur_z)
print(f"1) max Z подряд: {max_z}")

# 2. Макс подряд одинаковых в a
max_same = 1; cur_same = 1
for i in range(1, len(a)):
    cur_same = cur_same + 1 if a[i] == a[i-1] else 1
    max_same = max(max_same, cur_same)
# Найти все цепочки такой длины
chains2 = []
i = 0
while i < len(a):
    j = i
    while j < len(a) and a[j] == a[i]: j += 1
    if j - i == max_same:
        chains2.append((a[i], i, j-1))
    i = j
print(f"2) max одинаковых подряд: {max_same}, цепочки: {chains2}")

# 3. Макс подряд чередующихся (соседние различны)
max_alt = 1; cur_alt = 1
for i in range(1, len(a)):
    cur_alt = cur_alt + 1 if a[i] != a[i-1] else 1
    max_alt = max(max_alt, cur_alt)
print(f"3) max чередующихся подряд: {max_alt}")

# 4. Цепочки длины 3: c1 in ZX, c2 in XY и c2!=c1, c3 in YZ и c3!=c2
count4 = 0
for i in range(len(a)-2):
    c1, c2, c3 = a[i], a[i+1], a[i+2]
    if (c1 in ('Z','X')
            and c2 in ('X','Y') and c2 != c1
            and c3 in ('Y','Z') and c3 != c2):
        count4 += 1
print(f"4) цепочек длины 3 по условию: {count4}")

# 5. Макс цепочка X,Y (без Z) в a
max_xy = 0; cur_xy = 0
for c in a:
    cur_xy = cur_xy + 1 if c in ('X','Y') else 0
    max_xy = max(max_xy, cur_xy)
print(f"5) max цепочка X,Y: {max_xy}")

# 6. Макс цепочка XYZXYZ... в a
pattern = ['X','Y','Z']
max6 = 0; cur6 = 0; pos6 = 0
for c in a:
    if c == pattern[pos6 % 3]:
        pos6 += 1; cur6 += 1
    else:
        pos6 = 0; cur6 = 0
        if c == pattern[0]:
            pos6 = 1; cur6 = 1
    max6 = max(max6, cur6)
print(f"6) max цепочка XYZXYZ...: {max6}")

# 7. Символ чаще всего после E в b
after_e = [b[i+1] for i in range(len(b)-1) if b[i] == 'E']
cnt7 = Counter(after_e)
print(f"7) Распределение после E: {cnt7.most_common(5)}")
print(f"   Чаще всего: {cnt7.most_common(1)[0]}")

# 8. Макс расстояние между двумя одинаковыми в b
first_b = {}
max8 = 0; char8 = ''
for i, c in enumerate(b):
    if c in first_b:
        d = i - first_b[c]
        if d > max8: max8 = d; char8 = c
    else:
        first_b[c] = i
print(f"8) max расстояние между одина��овыми: {max8} (символ '{char8}')")

# 9. М��кс подряд символов без вхождения 'XZZY' в a
s9 = ''.join(a)
pat9 = 'XZZY'
import re
# Найдём позиции всех вхождений XZZY
occ9 = [m.start() for m in re.finditer(pat9, s9)]
print(f"   Вхождения XZZY (start): {occ9}")
# Максимальный сегмент без XZZY — расстояние между концом предыдущего и началом следующего
if not occ9:
    max9 = len(a)
else:
    segs = []
    prev = 0
    for p in occ9:
        segs.append(p - prev)
        prev = p + len(pat9)
    segs.append(len(s9) - prev)
    max9 = max(segs)
print(f"9) max подряд без XZZY: {max9}")
