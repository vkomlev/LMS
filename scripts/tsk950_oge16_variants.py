# -*- coding: utf-8 -*-
"""tsk-950: набор разных ВЕРНЫХ решений «как пишут девятиклассники» для 5 заданий.

Это замер, а не эталоны: каждое решение проверяется исполнением на примере из
условия и на случайных тестах против образцовой функции, затем считается, какая
доля из них совпадает с эталонами по code_ast.
"""
from __future__ import annotations

from typing import Dict, List

VARIANTS: Dict[int, List[str]] = {}

# 7226: максимальное число, кратное 5 (N, затем N чисел)
VARIANTS[7226] = [
    "n = int(input())\nm = 0\nfor i in range(n):\n    x = int(input())\n    if x % 5 == 0 and x > m:\n        m = x\nprint(m)\n",
    "N = int(input())\nmax5 = 0\nfor i in range(N):\n    a = int(input())\n    if a % 5 == 0:\n        if a > max5:\n            max5 = a\nprint(max5)\n",
    "n = int(input())\nmx = -1\nfor _ in range(n):\n    num = int(input())\n    if num % 5 == 0 and num > mx:\n        mx = num\nprint(mx)\n",
    "n = int(input())\nm = 0\nfor i in range(n):\n    x = int(input())\n    if x % 5 == 0:\n        m = max(m, x)\nprint(m)\n",
    "n = int(input())\nlst = []\nfor i in range(n):\n    lst.append(int(input()))\nres = 0\nfor x in lst:\n    if x % 5 == 0 and x > res:\n        res = x\nprint(res)\n",
    "n = int(input())\na = [int(input()) for i in range(n)]\nprint(max(x for x in a if x % 5 == 0))\n",
    "n = int(input())\na = [int(input()) for _ in range(n)]\nb = [x for x in a if x % 5 == 0]\nprint(max(b))\n",
    "n = int(input())\nmaxim = 0\ni = 0\nwhile i < n:\n    x = int(input())\n    if x % 5 == 0 and x > maxim:\n        maxim = x\n    i += 1\nprint(maxim)\n",
    "k = int(input())\nm = 0\nfor i in range(k):\n    c = int(input())\n    if c % 5 == 0 and c > m:\n        m = c\nprint(m)\n",
    "n = int(input())\nm = 0\nfor i in range(1, n + 1):\n    x = int(input())\n    if x % 5 == 0 and x >= m:\n        m = x\nprint(m)\n",
    "n = int(input())\nm = 0\nfor i in range(n):\n    x = int(input())\n    if x % 5 != 0:\n        continue\n    if x > m:\n        m = x\nprint(m)\n",
    "n = int(input())\nmax_num = 0\nfor i in range(n):\n    x = int(input())\n    if (x % 5 == 0) and (x > max_num):\n        max_num = x\nprint(max_num)\n",
    "n = int(input())\nm = 0\nfor i in range(n):\n    x = int(input())\n    if x % 5 == 0 and x > m: m = x\nprint(m)  # ответ\n",
]

# 7228: количество чисел, кратных 4
VARIANTS[7228] = [
    "n = int(input())\nk = 0\nfor i in range(n):\n    x = int(input())\n    if x % 4 == 0:\n        k += 1\nprint(k)\n",
    "n = int(input())\ncount = 0\nfor i in range(n):\n    a = int(input())\n    if a % 4 == 0:\n        count = count + 1\nprint(count)\n",
    "N = int(input())\ncnt = 0\nfor _ in range(N):\n    num = int(input())\n    if num % 4 == 0:\n        cnt += 1\nprint(cnt)\n",
    "n = int(input())\nc = 0\nfor i in range(n):\n    x = int(input())\n    if not x % 4:\n        c += 1\nprint(c)\n",
    "n = int(input())\na = [int(input()) for i in range(n)]\nprint(len([x for x in a if x % 4 == 0]))\n",
    "n = int(input())\na = [int(input()) for i in range(n)]\nk = 0\nfor x in a:\n    if x % 4 == 0:\n        k += 1\nprint(k)\n",
    "n = int(input())\nk = 0\ni = 0\nwhile i < n:\n    x = int(input())\n    if x % 4 == 0:\n        k += 1\n    i += 1\nprint(k)\n",
    "n = int(input())\nk = 0\nwhile n > 0:\n    x = int(input())\n    if x % 4 == 0:\n        k += 1\n    n -= 1\nprint(k)\n",
    "n = int(input())\nprint(sum(1 for i in range(n) if int(input()) % 4 == 0))\n",
    "n = int(input())\nk = 0\nfor i in range(n):\n    x = int(input())\n    k += x % 4 == 0\nprint(k)\n",
    "n = int(input())\nk = 0\nfor i in range(n):\n    x = int(input())\n    if x % 4 == 0:\n        k += 1\n    else:\n        pass\nprint(k)\n",
    "n = int(input())\ns = 0\nfor j in range(n):\n    b = int(input())\n    if b % 4 == 0:\n        s += 1\nprint(s)\n",
]

# 7233: сумма чисел, оканчивающихся на 4
VARIANTS[7233] = [
    "n = int(input())\ns = 0\nfor i in range(n):\n    x = int(input())\n    if x % 10 == 4:\n        s += x\nprint(s)\n",
    "n = int(input())\nsumma = 0\nfor i in range(n):\n    a = int(input())\n    if a % 10 == 4:\n        summa = summa + a\nprint(summa)\n",
    "N = int(input())\ns = 0\nfor _ in range(N):\n    x = int(input())\n    if str(x)[-1] == '4':\n        s += x\nprint(s)\n",
    "n = int(input())\ns = 0\nfor i in range(n):\n    x = input()\n    if x[-1] == '4':\n        s += int(x)\nprint(s)\n",
    "n = int(input())\na = [int(input()) for i in range(n)]\nprint(sum(x for x in a if x % 10 == 4))\n",
    "n = int(input())\na = [int(input()) for i in range(n)]\ns = 0\nfor x in a:\n    if x % 10 == 4:\n        s += x\nprint(s)\n",
    "n = int(input())\ns = 0\ni = 0\nwhile i < n:\n    x = int(input())\n    if x % 10 == 4:\n        s += x\n    i += 1\nprint(s)\n",
    "n = int(input())\ns = 0\nfor i in range(n):\n    x = int(input())\n    if x % 10 == 4:\n        s = s + x\nprint(s)\n",
    "n = int(input())\nres = 0\nfor i in range(n):\n    num = int(input())\n    if num % 10 == 4:\n        res += num\nprint(res)\n",
    "n = int(input())\ns = 0\nfor i in range(n):\n    x = int(input())\n    if x % 10 != 4:\n        continue\n    s += x\nprint(s)\n",
    "n = int(input())\ns = 0\nfor i in range(n):\n    x = int(input())\n    if (x - 4) % 10 == 0:\n        s += x\nprint(s)\n",
    "n = int(input())\ns = 0\nfor i in range(0, n):\n    x = int(input())\n    if x % 10 == 4:\n        s += x\nprint(s)\n",
]

# 7241: ввод до 0, сумма чисел, кратных 6 и оканчивающихся на 4
VARIANTS[7241] = [
    "s = 0\nx = int(input())\nwhile x != 0:\n    if x % 6 == 0 and x % 10 == 4:\n        s += x\n    x = int(input())\nprint(s)\n",
    "s = 0\nwhile True:\n    x = int(input())\n    if x == 0:\n        break\n    if x % 6 == 0 and x % 10 == 4:\n        s += x\nprint(s)\n",
    "summa = 0\na = int(input())\nwhile a != 0:\n    if a % 6 == 0 and a % 10 == 4:\n        summa = summa + a\n    a = int(input())\nprint(summa)\n",
    "s = 0\nx = int(input())\nwhile x > 0:\n    if x % 6 == 0 and x % 10 == 4:\n        s += x\n    x = int(input())\nprint(s)\n",
    "s = 0\nx = int(input())\nwhile x:\n    if x % 6 == 0 and x % 10 == 4:\n        s += x\n    x = int(input())\nprint(s)\n",
    "s = 0\nwhile True:\n    x = int(input())\n    if x == 0:\n        break\n    if x % 6 == 0:\n        if x % 10 == 4:\n            s += x\nprint(s)\n",
    "s = 0\nx = int(input())\nwhile x != 0:\n    if x % 30 == 24:\n        s += x\n    x = int(input())\nprint(s)\n",
    "s = 0\nx = int(input())\nwhile x != 0:\n    if x % 6 == 0 and str(x)[-1] == '4':\n        s += x\n    x = int(input())\nprint(s)\n",
    "a = []\nx = int(input())\nwhile x != 0:\n    a.append(x)\n    x = int(input())\nprint(sum(x for x in a if x % 6 == 0 and x % 10 == 4))\n",
    "s = 0\nn = int(input())\nwhile n != 0:\n    if n % 6 == 0 and n % 10 == 4:\n        s += n\n    n = int(input())\nprint(s)\n",
    "s = 0\nx = int(input())\nwhile x != 0:\n    if x % 10 == 4 and x % 6 == 0:\n        s += x\n    x = int(input())\nprint(s)\n",
    "s = 0\nwhile True:\n    x = int(input())\n    if x == 0:\n        break\n    elif x % 6 == 0 and x % 10 == 4:\n        s += x\nprint(s)\n",
]

# 7244: камера — минимальная скорость, YES если хоть одна > 80
VARIANTS[7244] = [
    "n = int(input())\nm = 301\nf = False\nfor i in range(n):\n    v = int(input())\n    if v < m:\n        m = v\n    if v > 80:\n        f = True\nprint(m)\nif f:\n    print('YES')\nelse:\n    print('NO')\n",
    "n = int(input())\nmn = 1000\nflag = False\nfor i in range(n):\n    x = int(input())\n    if x < mn:\n        mn = x\n    if x > 80:\n        flag = True\nprint(mn)\nif flag:\n    print('YES')\nelse:\n    print('NO')\n",
    "n = int(input())\na = [int(input()) for i in range(n)]\nprint(min(a))\nif max(a) > 80:\n    print('YES')\nelse:\n    print('NO')\n",
    "n = int(input())\na = [int(input()) for i in range(n)]\nprint(min(a))\nprint('YES' if max(a) > 80 else 'NO')\n",
    "n = int(input())\nm = 301\nk = 0\nfor i in range(n):\n    v = int(input())\n    m = min(m, v)\n    if v > 80:\n        k += 1\nprint(m)\nif k > 0:\n    print('YES')\nelse:\n    print('NO')\n",
    "n = int(input())\nm = int(input())\nf = m > 80\nfor i in range(n - 1):\n    v = int(input())\n    if v < m:\n        m = v\n    if v > 80:\n        f = True\nprint(m)\nif f:\n    print('YES')\nelse:\n    print('NO')\n",
    "N = int(input())\nminimum = 301\nans = 'NO'\nfor i in range(N):\n    s = int(input())\n    if s < minimum:\n        minimum = s\n    if s > 80:\n        ans = 'YES'\nprint(minimum)\nprint(ans)\n",
    "n = int(input())\nm = 301\nf = False\ni = 0\nwhile i < n:\n    v = int(input())\n    if v < m:\n        m = v\n    if v > 80:\n        f = True\n    i += 1\nprint(m)\nif f:\n    print('YES')\nelse:\n    print('NO')\n",
    "n = int(input())\na = []\nfor i in range(n):\n    a.append(int(input()))\nprint(min(a))\nif max(a) > 80:\n    print('YES')\nelse:\n    print('NO')\n",
    "n = int(input())\nm = 301\nf = 0\nfor i in range(n):\n    v = int(input())\n    if v < m:\n        m = v\n    if v > 80:\n        f = 1\nprint(m)\nif f == 1:\n    print('YES')\nelse:\n    print('NO')\n",
    "n = int(input())\nm = 301\nf = False\nfor i in range(n):\n    v = int(input())\n    if v < m:\n        m = v\n    if v > 80:\n        f = True\nprint(m)\nif f:\n    print(\"YES\")\nelse:\n    print(\"NO\")\n",
    "n = int(input())\nm = 301\nf = False\nfor _ in range(n):\n    v = int(input())\n    m = min(m, v)\n    f = f or v > 80\nprint(m)\nprint('YES' if f else 'NO')\n",
]
