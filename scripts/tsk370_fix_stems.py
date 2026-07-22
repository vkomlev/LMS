# -*- coding: utf-8 -*-
"""tsk-370: восстановить потерянный при импорте вопрос и убрать дубль преамбулы.

ЧТО И ПОЧЕМУ

Сплошной разбор 6302 активных заданий (`scripts/tsk370_scan.py`) плюс сверка условий с
источником по ID (`scripts/tsk370_verify_source.py`, 768 заданий kompege) дали пять правок.

A. ВОПРОС ПОТЕРЯН — условие обрывается на описании входных данных, ученик видит, что
   дано, но не видит, что искать. Дописывается дословный текст источника, начиная с
   первого абзаца, которого в LMS нет:

   * 2138 — kompege 20484 (задание 3, база данных «Автосервисы Екатеринбурга»);
   * 3106 — kompege 25344 (задание 5, троичная запись; вместе с вопросом потерян и
     разъясняющий пример, он обрывается тем же местом и возвращается тоже);
   * 3323 — kompege 2054 (задание 3, база данных «Оператор»);
   * 3378 — kompege 21412 (задание 13, маска сети).

B. ПРЕАМБУЛА ВСТАВЛЕНА ДВАЖДЫ — 3409 (`tg:ege:425`), известно из [[tsk-369]], где дубль
   намеренно не трогали. Удаляется ВТОРОЕ вхождение; первое и дописанный там вопрос
   остаются на месте.

ГЕЙТ ПРИВЯЗКИ К ИСТОЧНИКУ (как в [[tsk-369]]): дословный фрагмент условия совпадает
слово в слово + значимые числа те же + верный ответ LMS равен `key` источника. Для всех
четырёх заданий сошлись все три признака; проверка ответа повторяется скриптом перед
записью, и расхождение останавливает правку.

dry-run по умолчанию; `--apply` при DBCHECK_OK=1. Перед записью сверяется, что условие
в базе — ровно то, на котором собиралась правка (иначе отказ). После COMMIT — независимая
построчная проверка. Бэкап прежних значений пишется до записи.

Запуск: python scripts/tsk370_fix_stems.py --backup <файл.json> [--apply]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

import asyncpg

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tsk370_scan import dsn  # noqa: E402

# ---------------------------------------------------------------- A. дописать вопрос

APPEND: dict[int, dict] = {
    2138: {
        "kompege_id": "20484",
        "answer": "22784",
        # у 2138 условие обрывается ВНУТРИ картинки — сперва снимается обрезанный тег
        # (см. TRUNCATED_IMG), и только после этого якорь совпадает с хвостом
        "drop_truncated_img": True,
        # хвост условия в LMS перед правкой — якорь, что правим то самое задание
        "ends_with": "На рисунке приведена схема указанной базы данных.</p>",
        "html": (
            '\n<p class="MsoNormal">В Екатеринбурге резко выросли продажи запчастей для '
            'двигателей автомобилей. Городские власти заинтересованы в информации о том, '
            'какой автосервис продал наибольшее количество запчастей для двигателя за '
            'период с 1 сентября по 30 ноября включительно.</p>'
            '\n<p class="MsoNormal">В ответе запишите найденное наибольшее количество '
            'проданных запчастей.</p>'
        ),
    },
    3106: {
        "kompege_id": "25344",
        "answer": "243",
        "ends_with": "Результат переводится в десятичную систему и выводится на экран.</p>",
        "html": (
            '<p>Например, для исходного числа 8<sub>10</sub> = 22<sub>3</sub> результатом '
            'является число 22110<sub>3</sub> = 228<sub>10</sub>, а для исходного числа '
            '9<sub>10</sub> = 100<sub>3</sub> это число 10000<sub>3</sub> = 81<sub>10</sub></p>'
            '<p>Укажите минимальное нечётное число R, большее 208, которое может быть '
            'получено с помощью описанного алгоритма.<br>В ответе запишите это число в '
            'десятичной системе счисления.</p>'
        ),
    },
    3323: {
        "kompege_id": "2054",
        "answer": "17800",
        "ends_with": "На рисунке приведена схема базы данных.</p>",
        "html": (
            '\n<p>Используя информацию из приведённой базы данных, определите на какую '
            'сумму оператор оказал услуги хостинга, видеонаблюдения и установки антивируса '
            'жителям Нового района. В ответе запишите только число.</p>'
        ),
    },
    3378: {
        "kompege_id": "21412",
        "answer": "14316872222",
        "ends_with": "143.168.72.213 и сетевой маской 255.255.255.240.</p>",
        "html": (
            '\n<p>Определите наибольший IP-адрес данной сети, который может быть присвоен '
            'компьютеру. В ответе укажите найденный IP-адрес без разделителей.<br>Например, '
            'если бы найденный адрес был равен 111.22.3.44, то в ответе следовало бы '
            'записать 11122344.</p>'
        ),
    },
}

# ------------------------------------------------------------- B. снять дубль преамбулы

# Задание 3409: преамбула вставлена импортом дважды подряд. Удаляется второе вхождение
# вместе с разделителем `<br>`, которым импорт склеил копии.
DEDUPE_ID = 3409
DEDUPE_FRAGMENT = (
    "В файле содержится информация о совокупности N вычислительных процессов, которые "
    "могут выполняться параллельно или последовательно. Приостановка выполнения процесса "
    "не допускается. Будем говорить, что процесс B зависит от процесса A, если для "
    "выполнения процесса B необходимы результаты выполнения процесса A. В этом случае "
    "процессы A и B могут выполняться только последовательно. <br>Информация о процессах "
    "представлена в файле в виде таблицы. В первом столбце таблицы указан идентификатор "
    "процесса (ID), во втором столбце таблицы – время его выполнения в миллисекундах, в "
    "третьем столбце перечислены с разделителем «;» ID процессов, от которых зависит "
    "данный процесс. Если процесс независимый, то в таблице указано значение 0."
)


# ------------------------------------------------- C. снять обрезанный тег картинки

# Импорт обрывает условие посреди встроенной картинки (`data:image/...;base64,…`): тег
# `<img` остаётся незакрытым, base64 неполон, изображение не отображается ни в одном
# браузере, а всё, что дописать после, окажется внутри незакрытого атрибута и пропадёт.
# Снимается хвост от `<img` (вместе с пустым абзацем-обёрткой, если он есть) до конца.
TRUNCATED_IMG = re.compile(r"(?:\s*<p[^>]*>)?\s*<img\b[^>]*$", re.S)
DROP_IMG_IDS = (2138, 2207, 2208)


def drop_truncated_img(stem: str) -> str:
    """Условие без обрезанного тега картинки."""
    cut = TRUNCATED_IMG.sub("", stem)
    if cut == stem:
        raise RuntimeError("обрезанного тега картинки нет — условие в базе изменилось")
    if "<img" in cut[cut.rfind("<img"):] and ">" not in cut[cut.rfind("<img"):]:
        raise RuntimeError("после чистки остался ещё один незакрытый тег")
    return cut


def new_stem_append(stem: str, rule: dict) -> str:
    """Условие с дописанным хвостом источника."""
    if rule.get("drop_truncated_img"):
        stem = drop_truncated_img(stem)
    if not stem.rstrip().endswith(rule["ends_with"]):
        raise RuntimeError("условие в базе не то, на котором собиралась правка")
    return stem.rstrip() + rule["html"]


def new_stem_dedupe(stem: str) -> str:
    """Условие без второго вхождения преамбулы."""
    first = stem.find(DEDUPE_FRAGMENT)
    if first < 0:
        raise RuntimeError("преамбула не найдена — условие в базе изменилось")
    second = stem.find(DEDUPE_FRAGMENT, first + len(DEDUPE_FRAGMENT))
    if second < 0:
        raise RuntimeError("второго вхождения преамбулы нет — править нечего")
    if stem.find(DEDUPE_FRAGMENT, second + len(DEDUPE_FRAGMENT)) >= 0:
        raise RuntimeError("вхождений больше двух — разбирать руками")
    # разделитель `<br>` между копиями уходит вместе со второй копией
    start = second
    if stem[second - 4:second] == "<br>":
        start = second - 4
    return stem[:start] + stem[second + len(DEDUPE_FRAGMENT):]


async def main(backup_path: Path, apply: bool) -> None:
    ids = sorted(set(APPEND) | {DEDUPE_ID} | set(DROP_IMG_IDS))
    conn = await asyncpg.connect(dsn("learn_prod_db"))
    try:
        rows = {r["id"]: r for r in await conn.fetch(
            "SELECT id, external_uid, is_active, "
            "       task_content->>'stem' AS stem, "
            "       solution_rules #>> '{short_answer,accepted_answers,0,value}' AS answer "
            "FROM tasks WHERE id = ANY($1::int[])", ids)}
        missing = sorted(set(ids) - set(rows))
        if missing:
            raise RuntimeError(f"не нашёл заданий: {missing}")
        inactive = [i for i in ids if not rows[i]["is_active"]]
        if inactive:
            raise RuntimeError(f"задания неактивны, править нечего: {inactive}")

        plan: dict[int, str] = {}
        for tid, rule in APPEND.items():
            if (rows[tid]["answer"] or "").strip() != rule["answer"]:
                raise RuntimeError(
                    f"{tid}: ответ в базе {rows[tid]['answer']!r} разошёлся с "
                    f"источником {rule['answer']!r} — привязка не подтверждена")
            plan[tid] = new_stem_append(rows[tid]["stem"], rule)
        plan[DEDUPE_ID] = new_stem_dedupe(rows[DEDUPE_ID]["stem"])
        for tid in DROP_IMG_IDS:
            if tid not in plan:  # 2138 уже почищен внутри new_stem_append
                plan[tid] = drop_truncated_img(rows[tid]["stem"])

        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_text(json.dumps(
            [{"id": i, "external_uid": rows[i]["external_uid"], "stem": rows[i]["stem"]}
             for i in ids], ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"Бэкап прежних значений: {backup_path}\n")

        for tid in ids:
            was, now = rows[tid]["stem"], plan[tid]
            if tid == DEDUPE_ID:
                kind = "снять дубль преамбулы"
            elif tid in APPEND:
                kind = "снять обрезанный тег + дописать вопрос" \
                    if APPEND[tid].get("drop_truncated_img") else "дописать вопрос"
            else:
                kind = "снять обрезанный тег картинки"
            print(f"[{tid}] {rows[tid]['external_uid']} — {kind}: "
                  f"{len(was)} → {len(now)} символов ({len(now) - len(was):+d})")

        if not apply:
            print("\nDRY-RUN: в базу ничего не записано. Повторить с --apply.")
            return

        async with conn.transaction():
            for tid, stem in plan.items():
                await conn.execute(
                    "UPDATE tasks SET task_content = jsonb_set("
                    "  task_content, '{stem}', to_jsonb($2::text), true) "
                    "WHERE id = $1", tid, stem)
        print("\nCOMMIT выполнен. Независимая проверка после записи:")

        check = {r["id"]: r["stem"] for r in await conn.fetch(
            "SELECT id, task_content->>'stem' AS stem FROM tasks WHERE id = ANY($1::int[])",
            ids)}
        bad = [i for i in ids if check.get(i) != plan[i]]
        for tid in ids:
            print(f"  [{tid}] {'СОВПАЛО' if check.get(tid) == plan[tid] else 'РАСХОЖДЕНИЕ'}"
                  f" — {len(check.get(tid) or '')} символов")
        if bad:
            raise RuntimeError(f"после COMMIT не совпало: {bad}")
    finally:
        await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--backup", type=Path, required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    asyncio.run(main(args.backup, args.apply))
