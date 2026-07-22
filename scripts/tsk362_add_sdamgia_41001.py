# -*- coding: utf-8 -*-
"""tsk-362: завести задание sdamgia:41001 (ЕГЭ-26, UNIX-время), сложность HARD.

ЗАЧЕМ
Решение оператора 2026-07-22. Пост канала 277 разбирал сразу две задачи-аналога —
sdamgia:40742 и sdamgia:41001. Первая в LMS есть (задание 3774), вторая отсутствовала;
само задание-пост (3455) деактивировано, а его видеоразбор перенесён на 3774. Теперь
заводим и вторую задачу отдельно, чтобы разбор работал на обе.

Условия у них различаются одной неделей наблюдения: 1633305600 у 40742 против 1634515200
у 41001 — то есть это действительно разные задачи с разными ответами.

ОТВЕТ ПРОВЕРЕН СОБСТВЕННЫМ РЕШЕНИЕМ
Файл с данными скачан у источника (`/get_file?id=99375`, 932 КБ, 47 408 процессов), задача
решена: максимум 7768 одновременных процессов, суммарно 20 секунд → «7768 20». Совпало с
ответом sdamgia («Ответ: 7768&20»).

Ответ многозначный, поэтому кладётся так же, как остальные табличные: `answer_raw` +
`pending_tbl_com=true` + обязательная ручная проверка, до появления типа `TBL_COM` ([[tsk-366]]).

ССЫЛКА НА ФАЙЛ — АБСОЛЮТНАЯ
У соседнего задания 3774 ссылка на файл осталась относительной (`/get_file?id=99001`), то есть
из LMS она никуда не ведёт — это часть [[tsk-369]]. Здесь ссылка сразу абсолютная, на домен
источника, чтобы ученик мог скачать данные.

ПОРЯДОК В КУРСЕ
После вставки задание получает `order_position` от триггера (max+1). Сложность HARD, а HARD в
курсе 153 и так идёт последним блоком, поэтому дополнительный реордер не нужен — но скрипт
проверяет инвариант (межгрупповой порядок и отсутствие коллизий) и падает, если он нарушен.

Запуск: dry-run по умолчанию;
  python scripts/tsk362_add_sdamgia_41001.py
  DBCHECK_OK=1 python scripts/tsk362_add_sdamgia_41001.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import asyncpg

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from app.schemas.solution_rules import SolutionRules  # noqa: E402

COURSE_ID = 153
EXTERNAL_UID = "sdamgia:41001"
DIFFICULTY_HARD = 4
ANSWER = "7768 20"
FILE_URL = "https://inf-ege.sdamgia.ru/get_file?id=99375"
SOURCE_URL = "https://inf-ege.sdamgia.ru/problem?id=41001"
VIDEO = "https://vk.com/video-53400615_456239635"

STEM = (
    '<p>Во многих компьютерных системах текущее время хранится в формате «UNIX-время» — '
    'количестве секунд от начала суток 1 января 1970 года.</p>'
    '<p>В одной компьютерной системе проводили исследование загруженности. Для этого в течение '
    'месяца с момента UNIX-времени 1633046400 фиксировали и заносили в базу данных моменты '
    'старта и финиша всех процессов, действовавших в этой системе.</p>'
    '<p>Вам необходимо определить, какое наибольшее количество процессов выполнялось в системе '
    'одновременно на неделе, начавшейся в момент UNIX-времени 1634515200, и в течение какого '
    'суммарного времени (в секундах) выполнялось такое наибольшее количество процессов.</p>'
    f'<p><b>Входные данные:</b> <a href="{FILE_URL}" target="_blank">файл с данными задания 26</a></p>'
    '<p>Первая строка входного файла содержит целое число <i>N</i> — общее количество процессов '
    'за весь период наблюдения. Каждая из следующих <i>N</i> строк содержит 2 целых числа: время '
    'старта и время завершения одного процесса в виде UNIX-времени. Все данные в строках входного '
    'файла отделены одним пробелом.</p>'
    '<p>Если в качестве времени старта указан ноль, это означает, что процесс был активен в момент '
    'начала исследования. Если в качестве времени завершения указан ноль, это означает, что процесс '
    'не завершился к моменту окончания исследования.</p>'
    '<p>При совпадающем времени считается, что все старты и завершения процессов происходят '
    'одновременно, в начале соответствующей секунды. В частности, если время старта одного процесса '
    'совпадает с временем завершения другого и других стартов и завершений в этот момент нет, то '
    'количество активных процессов в этот момент не изменяется.</p>'
    '<p>В ответе запишите два целых числа: сначала максимальное количество процессов, которые '
    'выполнялись одновременно на неделе, начиная с момента UNIX-времени 1634515200, затем суммарное '
    'количество секунд, в течение которых на этой неделе выполнялось такое максимальное количество '
    'процессов.</p>'
)


def _dsn() -> str:
    env = os.environ.get("LEARN_PROD_DSN") or os.environ.get("DATABASE_URL", "")
    dsn = env.replace("postgresql+asyncpg://", "postgresql://")
    if "5.42.107.253" not in dsn:
        for candidate in (project_root / ".mcp.json", Path(r"D:\Work\LMS\.mcp.json")):
            if not candidate.exists():
                continue
            cfg = json.loads(candidate.read_text(encoding="utf-8"))
            servers = cfg.get("mcpServers", cfg)
            for arg in servers["learn_prod_db"]["args"]:
                if isinstance(arg, str) and arg.startswith("postgresql://") and "5.42.107.253" in arg:
                    dsn = arg
                    break
    if "5.42.107.253" not in dsn or "/learn" not in dsn:
        raise RuntimeError("Не нашёл прод-DSN learn (5.42.107.253/learn).")
    return dsn


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            dup = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE external_uid = $1 "
                "OR (task_content->>'source_kind' = 'sdamgia' AND task_content->>'source_task_id' = '41001')",
                EXTERNAL_UID)
            if dup:
                raise AssertionError(f"задание с sdamgia:41001 уже есть ({dup} шт.) — не дублирую")

            # Курс-донор берём у задания-близнеца, чтобы курс и course_uid точно совпали.
            twin = await conn.fetchrow(
                "SELECT course_id, task_content->>'course_uid' AS uid, max_score, requirement_level "
                "FROM tasks WHERE external_uid = 'wp_nav:26:9d60fd95'")
            if twin is None or twin["course_id"] != COURSE_ID:
                raise AssertionError("не нашёл задание-близнец 40742 в курсе 153")

            task_content = {
                "code": None, "stem": STEM, "tags": [], "type": "SA_COM", "media": [],
                "title": None, "prompt": "", "options": [], "has_hints": True,
                "answer_raw": ANSWER, "course_uid": twin["uid"], "hints_text": [],
                "source_url": SOURCE_URL, "hints_video": [VIDEO], "source_kind": "sdamgia",
                "stem_images": [], "source_task_id": "41001", "difficulty_code": "HARD",
                "pending_tbl_com": True, "manual_review_required": True,
            }
            rules = SolutionRules(max_score=1, scoring_mode="all_or_nothing",
                                  auto_check=True, manual_review_required=True).model_dump()

            new_id = await conn.fetchval(
                "INSERT INTO tasks (external_uid, course_id, difficulty_id, max_score, "
                "task_content, solution_rules, is_active, requirement_level) "
                "VALUES ($1, $2, $3, 1, $4::jsonb, $5::jsonb, true, $6) RETURNING id",
                EXTERNAL_UID, COURSE_ID, DIFFICULTY_HARD,
                json.dumps(task_content, ensure_ascii=False), json.dumps(rules),
                twin["requirement_level"])
            row = await conn.fetchrow(
                "SELECT id, course_id, difficulty_id, order_position, is_active, "
                "task_content->>'answer_raw' AS raw, (task_content->>'pending_tbl_com')::bool AS tbl "
                "FROM tasks WHERE id = $1", new_id)
            print(f"Создано задание id={row['id']} курс={row['course_id']} "
                  f"сложность={row['difficulty_id']} позиция={row['order_position']} "
                  f"ответ={row['raw']!r} ждёт TBL_COM={row['tbl']}")

            # ---- Верификация порядка в курсе ----
            dupes = await conn.fetchval(
                "SELECT count(*) FROM (SELECT order_position FROM tasks WHERE course_id = $1 "
                "GROUP BY order_position HAVING count(*) > 1) x", COURSE_ID)
            if dupes:
                raise AssertionError(f"коллизии order_position в курсе {COURSE_ID}: {dupes}")
            violations = await conn.fetchval("""
                SELECT count(*) FROM (
                    SELECT difficulty_id, LAG(difficulty_id) OVER (ORDER BY order_position) AS prev
                    FROM tasks WHERE course_id = $1 AND is_active
                ) x WHERE prev IS NOT NULL AND difficulty_id < prev""", COURSE_ID)
            if violations:
                raise AssertionError(f"нарушен межгрупповой порядок в курсе {COURSE_ID}: {violations}")
            print(f"Проверка курса {COURSE_ID}: 0 коллизий, 0 нарушений порядка")

            print("\nOK: все проверки пройдены.")
            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (запусти с --apply при DBCHECK_OK=1)")
        print("\nЗАПИСАНО И ЗАКОММИЧЕНО.")
    finally:
        await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    try:
        asyncio.run(main(args.apply))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
