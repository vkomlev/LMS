# -*- coding: utf-8 -*-
"""tsk-392, проверка после записи: пересчитать ответы заданий ОГЭ-14 ПО ПРИВЯЗАННОМУ ФАЙЛУ.

ЗАЧЕМ
Все прочие сверки косвенные: совпал ID, совпал sha с близнецом, совпали якоря условия.
Прямое доказательство того, что к заданию привязан ИМЕННО ЕГО файл, ровно одно — по файлу
получается эталонный ответ задания. Задание ОГЭ-14 («обработка данных в электронной
таблице») это позволяет: условие формализуемо («сколько учеников округа X сдавали предмет
Y», «средний балл по Y»), а таблица маленькая.

Именно этой проверки не хватило в tsk-390: там у 17 авторских заданий сверялась лишь
СТРУКТУРА файла («лист Товар несёт Артикул»), одинаковая у обеих версий базы, — и чужой
файл прошёл гейт. Пересчёт ответа такую подмену ловит сразу.

Файл берётся боевым `GET /api/v1/media/<sha>` — то есть проверяется ровно то, что получит
ученик, а не локальная копия.

Read-only: SELECT из прода + HTTP GET. Ничего не пишет.

Запуск:
  python scripts/tsk392_verify_oge14.py
"""
from __future__ import annotations

import asyncio
import io
import os
import re
import sys
import urllib.request
import zipfile
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tsk369_collect import dsn  # noqa: E402

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

MEDIA_URL = "https://api.learn.victor-komlev.ru/api/v1/media/{}"

SQL = r"""
SELECT id, external_uid,
       (regexp_match(task_content->>'stem', '/api/v1/media/([0-9a-f]{64}\.ods)'))[1] AS sha_ext,
       solution_rules#>>'{short_answer,accepted_answers,0,value}' AS answer,
       regexp_replace(task_content->>'stem', '<[^>]+>', ' ', 'g') AS stem
FROM tasks
WHERE is_active AND course_id = 1179 AND external_uid LIKE 'oge:reshu:%'
ORDER BY id
"""


def read_ods(data: bytes) -> list[list[str]]:
    """Строки таблицы из .ods. Ячейки бывают «сжаты» атрибутом number-columns-repeated."""
    xml = zipfile.ZipFile(io.BytesIO(data)).read("content.xml").decode("utf-8")
    table = []
    for row in re.findall(r"<table:table-row.*?</table:table-row>", xml, re.S):
        cells: list[str] = []
        for attrs, body in re.findall(
                r"<table:table-cell(.*?)(?:/>|>(.*?)</table:table-cell>)", row, re.S):
            m = re.search(r'number-columns-repeated="(\d+)"', attrs)
            repeat = min(int(m.group(1)) if m else 1, 50)
            cells.extend([re.sub(r"<[^>]+>", "", body or "")] * repeat)
        table.append(cells)
    return table


def as_float(s: str) -> float | None:
    s = (s or "").replace("\xa0", "").replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


# Схема таблицы, которую разбирает этот скрипт: «округ | фамилия | предмет | балл».
# Задания ОГЭ-14 используют и другие схемы («Фамилия|Имя|Класс|Рост|Вес», «Город|Страна|
# Время в пути»), и разбирать их условия автоматически смысла нет — вопросы там всякий раз
# свои. Чужую схему скрипт обязан честно назвать неразобранной: иначе он посчитает по не
# тем столбцам и выдаст «РАСХОЖДЕНИЕ» там, где файл на самом деле верный.
KNOWN_HEADER = ("округ", "фамилия", "предмет", "балл")


def compute(stem: str, table: list[list[str]]) -> tuple[str | None, str]:
    """Посчитать ответ по условию и таблице. Возвращает (значение, как считали)."""
    header = [c.strip().lower() for c in (table[0] if table else [])][:4]
    if tuple(header) != KNOWN_HEADER:
        return None, f"другая схема таблицы ({', '.join(header) or 'пусто'}) — считать нечем"
    body = [r for r in table[1:] if len(r) >= 4]
    text = re.sub(r"\s+", " ", stem)

    # Округ задаётся буквой в скобках: «Восточном округе (В)». Буква — то, что лежит в
    # столбце A; название округа в тексте с ней не всегда согласовано по написанию.
    m_okrug = re.search(r"округ\w*\s*\(([А-ЯA-Z]{1,2})\)", text)
    okrug = m_okrug.group(1) if m_okrug else None
    m_subj = re.search(r"по\s+([а-яё]+)(?:\b|,)", text.lower())
    subject = None
    for word in ("информатик", "физик", "математик", "русск", "истори", "хими", "биолог",
                 "географ", "литератур", "общество", "английск", "физкультур"):
        if word in text.lower():
            subject = word
            break

    def rows() -> list[list[str]]:
        out = body
        if okrug:
            out = [r for r in out if r[0].strip() == okrug]
        if subject:
            out = [r for r in out if subject in r[2].lower()]
        return out

    picked = rows()
    if re.search(r"средн\w+ (?:тестов\w+ )?балл", text.lower()):
        vals = [v for v in (as_float(r[3]) for r in picked) if v is not None]
        if not vals:
            return None, "нет строк под фильтр"
        return f"{sum(vals) / len(vals):.2f}", f"среднее по {len(vals)} строкам (округ={okrug}, предмет={subject})"

    m_gt = re.search(r"(?:более|больше)\s+(\d+)\s*балл", text.lower())
    if m_gt:
        limit = float(m_gt.group(1))
        n = sum(1 for r in picked if (as_float(r[3]) or -1) > limit)
        return str(n), f"счёт >{limit:g} (округ={okrug}, предмет={subject})"

    if re.search(r"сколько\s+учеников", text.lower()):
        return str(len(picked)), f"счёт строк (округ={okrug}, предмет={subject})"
    return None, "условие не разобрано"


def same(expected: str | None, got: str | None) -> bool:
    if expected is None or got is None:
        return False
    a = as_float(expected)
    b = as_float(got)
    if a is None or b is None:
        return expected.strip() == got.strip()
    return abs(a - b) < 0.005


async def main() -> int:
    conn = await asyncpg.connect(dsn("learn_prod_db"))
    try:
        rows = await conn.fetch(SQL)
    finally:
        await conn.close()

    cache: dict[str, list[list[str]]] = {}
    ok = bad = skipped = 0
    problems = []
    for r in rows:
        if not r["sha_ext"]:
            skipped += 1
            print(f"  [нет файла] id={r['id']}")
            continue
        if r["sha_ext"] not in cache:
            req = urllib.request.Request(MEDIA_URL.format(r["sha_ext"]),
                                         headers={"User-Agent": "tsk392-verify/1.0"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                cache[r["sha_ext"]] = read_ods(resp.read())
        got, how = compute(r["stem"], cache[r["sha_ext"]])
        if got is None:
            skipped += 1
            print(f"  [не разобрал] id={r['id']} — {how}")
            continue
        if same(r["answer"], got):
            ok += 1
        else:
            bad += 1
            problems.append((r["id"], r["external_uid"], r["answer"], got, how))
            print(f"  [РАСХОЖДЕНИЕ] id={r['id']} эталон={r['answer']!r} по файлу={got!r} — {how}")

    print(f"\nПересчитано по привязанному файлу: совпало {ok}, расхождений {bad}, "
          f"не разобрано {skipped} (всего {len(rows)})")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
