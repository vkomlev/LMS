# -*- coding: utf-8 -*-
"""tsk-740, партия 12: гейт исполнимости кода уроков блока 23.

Код берётся ПРЯМО из HTML уроков (блоки <pre><code class="language-python">), без
перепечатки: проверяется ровно то, что увидит ученик. Из блоков собираются программы,
каждая запускается отдельным процессом Python в папке, где лежит 23.txt, и сравнивается
вывод:
  - пример ФИПИ из условия (ответы урока: 7, 3, 10, 12, (1, 7, 100) ...);
  - файл демоверсии ФИПИ (10971 и ответы Полякова, перепроверенные 30.09);
  - боевой файл задания s1 (урок «Собираем программу целиком» обещает 784).
Любое расхождение — код возврата 1.

Запуск: python scripts/tsk740_block23_code_check.py
"""
from __future__ import annotations

import html
import logging
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

SCRIPTS = Path(__file__).resolve().parent
UROKI = SCRIPTS / "tsk740_block23_lessons"
ROOT = SCRIPTS.parent
SNIMOK = ROOT / "reviews" / "tsk740-block23-snapshot-2026-09-30-do"
DEMO = ROOT / "reviews" / "2026-09-01-tsk740-fipi-demo23.txt"
S1_URL = "https://api.learn.victor-komlev.ru/api/v1/media/{}"
PRIMER = "100 12 1.0\n6 7 7.0\n6 1 1.0\n1 7 5.5\n7 100 2.0\n4 100 8.0\n1 100 12.0\n1 4 2.5\n"


def bloki(put: Path) -> list[str]:
    """Блоки Python-кода урока в том виде, как их видит ученик."""
    tekst = put.read_text(encoding="utf-8")
    return [html.unescape(b) for b in
            re.findall(r'<pre><code class="language-python">(.*?)</code></pre>', tekst, re.S)]


def zapustit(programma: str, dannye: str) -> tuple[int, str]:
    """Запустить программу в чистой папке с 23.txt; вернуть (код, вывод)."""
    with tempfile.TemporaryDirectory() as papka:
        Path(papka, "23.txt").write_text(dannye, encoding="utf-8")
        Path(papka, "p.py").write_text(programma, encoding="utf-8")
        sreda = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        r = subprocess.run([sys.executable, "p.py"], cwd=papka, capture_output=True,
                           text=True, encoding="utf-8", timeout=120, env=sreda)
        return r.returncode, (r.stdout + r.stderr).strip()


def s1_fajl() -> str:
    """Боевой файл задания s1 (10259) — по ссылке из снимка условия."""
    import json
    zad = json.loads((SNIMOK / "tasks.json").read_text(encoding="utf-8"))
    stem = next(z["task_content"]["stem"] for z in zad if z["external_uid"] == "lms:tsk740:gen23:s1")
    sha = re.search(r"/api/v1/media/([0-9a-f]{64}\.txt)", stem).group(1)
    req = urllib.request.Request(S1_URL.format(sha), headers={"User-Agent": "tsk740/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8")


def main() -> int:
    chtenie = bloki(SNIMOK / "m3901.html")[0]           # чтение файла в граф
    vershiny = bloki(SNIMOK / "m3901.html")[1]          # множество вершин
    # третий блок урока продолжает первый: импорт defaultdict стоит там
    chtenie_s_rebrami = "from collections import defaultdict\n" + bloki(SNIMOK / "m3901.html")[2]
    rek = bloki(UROKI / "m_rek.html")[0]
    dejk = bloki(UROKI / "m3902.html")[0]
    putej, putej_bez = bloki(UROKI / "m3904.html")[:2]
    celaya = bloki(UROKI / "m3906.html")[0]
    var = bloki(UROKI / "m_var.html")
    via, obhod, minw, maxk, put = var
    funk_rek = rek.rsplit("print(", 1)[0]                # функция без печати примера

    demo, s1 = DEMO.read_text(encoding="utf-8"), s1_fajl()
    sluchai = [
        # (что, программа, данные, ожидаемый вывод)
        ("рекурсия: пример", chtenie + "\n" + rek, PRIMER, "7"),
        ("рекурсия: демо", chtenie + "\n" + rek, demo, "10971"),
        ("Дейкстра: пример", chtenie + "\n" + dejk + "\nprint(кратчайший(граф, 1, 100))", PRIMER, "7.5"),
        ("Дейкстра: демо", chtenie + "\n" + dejk + "\nprint(int(кратчайший(граф, 1, 100)))", demo, "10971"),
        ("путей: пример", chtenie + "\n" + putej, PRIMER, "3"),
        ("путей без рекурсии: пример", chtenie_s_rebrami + "\n" + vershiny + "\n" + putej_bez +
         "\nprint(путей_без_рекурсии(рёбра, вершины, 1, 100))", PRIMER, "3"),
        ("путей: демо = без рекурсии", chtenie_s_rebrami + "\n" + vershiny + "\n" + putej_bez +
         "\n" + putej.replace("print(путей(1, 100))", "print(путей(1, 100) == путей_без_рекурсии(рёбра, вершины, 1, 100))"),
         demo, "True"),
        ("целая программа: s1", celaya, s1, "784"),
        ("целая программа: пример", celaya.replace("691, 893", "1, 100"), PRIMER, "7"),
        ("целая программа: нет пути", celaya.replace("691, 893", "100, 1"), PRIMER,
         "OverflowError: cannot convert float infinity to integer"),
        ("через N: пример", chtenie + "\n" + funk_rek + "\n" + via, PRIMER, "10"),
        ("через N: демо (Поляков)", chtenie + "\n" + funk_rek + "\n" + via, demo, "16058"),
        ("в обход: пример", "from collections import defaultdict\n" + obhod + "\n" + funk_rek +
         "\nprint(int(лучший(1, 100)))", PRIMER, "10"),
        ("в обход: демо (Поляков)", "from collections import defaultdict\n" +
         obhod.replace("{7}", "{633}") + "\n" + funk_rek + "\nprint(int(лучший(1, 100)))", demo, "11782"),
        ("вес от L: пример", chtenie + "\nfrom functools import cache\n" + minw +
         "\nprint(int(лучший(1, 100)))", PRIMER, "12"),
        ("вес от L: демо (Поляков)", chtenie + "\nfrom functools import cache\n" +
         minw.replace("L = 3", "L = 1000") + "\nprint(int(лучший(1, 100)))", demo, "23814"),
        ("не больше K: пример", chtenie + "\nfrom functools import cache\n" + maxk, PRIMER, "12"),
        ("не больше K: демо (Поляков)", chtenie + "\nfrom functools import cache\n" +
         maxk.replace("(1, 100, 1)", "(1, 100, 6)"), demo, "20198"),
        ("сам путь: пример", chtenie + "\nfrom functools import cache\n" + put, PRIMER, "(1, 7, 100)\n7"),
        ("сам путь: демо (Поляков)", chtenie + "\nfrom functools import cache\n" + put, demo, None),
        ("путей через N: пример", chtenie + "\n" + putej.rsplit("print(", 1)[0] +
         "\nprint(путей(1, 4) * путей(4, 100))", PRIMER, "1"),
    ]
    oshibok = 0
    for chto, prog, dannye, zhdem in sluchai:
        kod, vyvod = zapustit(prog, dannye)
        if zhdem is None:  # сверяем только последнюю строку
            ok = vyvod.splitlines()[-1] == "4673"
        elif zhdem.startswith("OverflowError"):
            ok = kod != 0 and vyvod.splitlines()[-1] == zhdem
        else:
            ok = kod == 0 and vyvod == zhdem
        oshibok += not ok
        log.info("%s %-30s -> %s", "OK  " if ok else "FAIL", chto, vyvod.splitlines()[-1] if vyvod else "")
    log.info("\nПроверено программ: %d, расхождений: %d", len(sluchai), oshibok)
    return 1 if oshibok else 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
