# -*- coding: utf-8 -*-
"""tsk-317/tsk-319 (F3+F6): восстановить ответы 36 Крылов-заданий ЕГЭ (варианты 1/5/11/16).

ЧТО ДЕЛАЕТ
36 активных заданий Сборника Крылова С.С. (external_uid LIKE 'crylov:%', варианты
1/5/11/16 — весь текущий LMS-охват) имеют solution_rules=null, answer_raw пуст,
картинок нет (аудит tsk-299 F3/F6). Скрипт заводит solution_rules с эталонным
ответом для ВСЕХ 36 и вставляет <img> в task_content.stem для 3 заданий, чьё
условие ссылается на рисунок/таблицу (граф/фрагмент таблицы истинности).

ИСТОЧНИК ОТВЕТОВ — не CB (не доверять!)
CB `external_tasks.task` (source pdf:d4:pdf:crylov:vN:20260602) содержит
автоматически распознанные ответы из того же PDF, но OCR таблицы ответов
(с. 245-249 книги) для вариантов 5/11/16 массово испорчен (значения вида
"or", "ae", "sooo" — не совпадают с реальными числами). Совпадает только
вариант 1 (проверено: 18/18 значений сверено с реальной таблицей +
future-step.ru независимо подтвердил v1t16=6). Поэтому ответы для этого
скрипта взяты НЕ из CB, а прочитаны напрямую с отрендеренных страниц PDF
(`tests/fixtures/external_tasks/pdf/Крылов. ЕГЭ по информатике 2026.pdf`,
раздел «ОТВЕТЫ», физические страницы 239-242 = печатные 245-248) и
перепроверены построчно (variant×task) по таблице. Картинки для t1/t2 —
кроп тех же PDF-страниц, залиты в CAS/S3 через
monolith.external_tasks.media.cas_downloader.store_bytes_to_cas (CB),
подтверждена публичная доступность через прод /api/v1/media/.

9 ЗАДАНИЙ ОСТАЮТСЯ БЕЗ ФАЙЛА-ПРИЛОЖЕНИЯ (не путать с "не восстановлено")
Задания 3, 9, 10, 17, 18, 22, 24, 26, 27 в этом сборнике used с файлами-
приложениями (таблицы БД, текстовые/табличные данные), которые НЕ встроены
в PDF — они распространяются через сайт ege.plus под кодом доступа с
голограммы бумажного издания (см. сноску в PDF). У нас этого кода нет.
Для таких заданий (v1t3, v5t17, v11t3, v11t9, v11t17, v11t24, v11t26,
v16t17, v16t24 — 9 шт.) ответ восстановлен (тот же верифицированный
источник), автопроверка будет работать корректно, НО ученик не сможет
самостоятельно решить задание без файла-приложения — это зафиксированная,
не скрытая деградация (сам файл не выдуман и не восстановлен).

Нормализация: ["trim","lower"] для одиночных ответов (доминирующая
конвенция, tsk-325 F1); ["trim","lower","collapse_spaces"] для составных
ответов из нескольких чисел через пробел (t17/t25/t26-типа) — устойчивее
к лишним пробелам при вводе, само значение не меняет логику сравнения.

ИДЕМПОТЕНТНОСТЬ / BLAST-RADIUS
UPDATE только для заданий из явного списка ID и только пока solution_rules
ещё null (WHERE-guard) — повторный запуск ничего не изменит. 0 попыток
учеников по этим заданиям (task_results). Обратимо (solution_rules → null,
<img> строка убирается по тому же анкеру).

Запуск: dry-run по умолчанию (транзакция откатывается); --apply — запись
(нужен DBCHECK_OK=1, прод-хост 5.42.107.253).
"""
from __future__ import annotations

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

from app.schemas.solution_rules import (  # noqa: E402
    SolutionRules,
    ShortAnswerRules,
    ShortAnswerAccepted,
)

MEDIA_BASE = "/api/v1/media"

# ─── Верифицированные ответы (id, external_uid, value, file_gated) ──────────
# file_gated=True — задание "3, 9, 10, 17, 18, 22, 24, 26, 27" (файл-приложение
# на ege.plus, недоступен нам); ответ всё равно верен и восстановлен.
TARGETS = [
    (4550, "crylov:v1t1", "14", False),
    (4551, "crylov:v1t2", "wxzy", False),
    (4553, "crylov:v1t3", "465", True),
    (4555, "crylov:v1t5", "411", False),
    (4559, "crylov:v1t8", "4518", False),
    (4573, "crylov:v1t16", "6", False),
    (4579, "crylov:v1t19", "244", False),
    (4557, "crylov:v5t6", "30", False),
    (4561, "crylov:v5t11", "137", False),
    (4562, "crylov:v5t12", "130", False),
    (4564, "crylov:v5t13", "1663", False),
    (4567, "crylov:v5t14", "5768", False),
    (4570, "crylov:v5t15", "82", False),
    (4576, "crylov:v5t17", "704 197847", True),
    (4554, "crylov:v11t3", "1099", True),
    (4556, "crylov:v11t5", "66", False),
    (4558, "crylov:v11t6", "80", False),
    (4560, "crylov:v11t9", "42", True),
    (4563, "crylov:v11t12", "51", False),
    (4565, "crylov:v11t13", "176", False),
    (4568, "crylov:v11t14", "11727433732", False),
    (4571, "crylov:v11t15", "43", False),
    (4574, "crylov:v11t16", "12271520", False),
    (4577, "crylov:v11t17", "980 17924", True),
    (4582, "crylov:v11t24", "5678", True),
    (4584, "crylov:v11t25", "11908813 2303 71995833 13923 81975863 15853 91955893 17783", False),
    (4585, "crylov:v11t26", "108420 16507", True),
    (4552, "crylov:v16t2", "ywzx", False),
    (4566, "crylov:v16t13", "224", False),
    (4569, "crylov:v16t14", "1236", False),
    (4572, "crylov:v16t15", "6", False),
    (4575, "crylov:v16t16", "41518080", False),
    (4578, "crylov:v16t17", "2936 75058186", True),
    (4580, "crylov:v16t19", "29", False),
    (4581, "crylov:v16t23", "1760", False),
    (4583, "crylov:v16t24", "10007", True),
]

# ─── Картинки: (id, anchor-подстрока в stem, sha_ext) ────────────────────────
# anchor встречается ровно 1 раз в текущем stem; <img> вставляется сразу после.
IMAGES = [
    (
        4550,  # crylov:v1t1 — граф + таблица расстояний
        "(в километрах).<br>",
        "1128b1abae17f72d2c73843abcbac2e7e991ff255cf7d86fed62cc4376bd1743.png",
    ),
    (
        4551,  # crylov:v1t2 — фрагмент таблицы истинности (3 строки)
        "переменных w, х, у, z.<br>",
        "0e2d97dd707a38b23a0bae0d2012da64992bd43a007992ccd471de70aa275f90.png",
    ),
    (
        4552,  # crylov:v16t2 — фрагмент таблицы истинности (3 строки)
        "переменных w,x,y,z.<br>",
        "0c4c4534f072c5ca98ce4ed97418327f0293be30ed47ac4996c8f0c5e762bf4c.png",
    ),
]

MULTI_VALUE_IDS = {4576, 4577, 4584, 4585, 4578}  # collapse_spaces


def build_payload(value: str, max_score: int, multi: bool) -> dict:
    normalization = ["trim", "lower", "collapse_spaces"] if multi else ["trim", "lower"]
    rules = SolutionRules(
        max_score=max_score,
        scoring_mode="all_or_nothing",
        auto_check=True,
        manual_review_required=False,
        short_answer=ShortAnswerRules(
            normalization=normalization,
            accepted_answers=[ShortAnswerAccepted(value=value, score=max_score)],
        ),
    )
    return rules.model_dump()


def _dsn() -> str:
    env = os.environ.get("LEARN_PROD_DSN") or os.environ.get("DATABASE_URL", "")
    dsn = env.replace("postgresql+asyncpg://", "postgresql://")
    if "5.42.107.253" not in dsn:
        cfg = json.loads((project_root / ".mcp.json").read_text(encoding="utf-8"))
        servers = cfg.get("mcpServers", cfg)
        for arg in servers["learn_prod_db"]["args"]:
            if isinstance(arg, str) and arg.startswith("postgresql://") and "5.42.107.253" in arg:
                dsn = arg
                break
    if "5.42.107.253" not in dsn or "/learn" not in dsn:
        raise RuntimeError(
            "Не нашёл прод-DSN learn (5.42.107.253/learn). Передай LEARN_PROD_DSN явно."
        )
    return dsn


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            ids = [t[0] for t in TARGETS]

            rows = await conn.fetch(
                "SELECT id, external_uid, max_score, "
                "(solution_rules IS NULL OR jsonb_typeof(solution_rules)='null') AS sr_is_null, "
                "task_content->>'stem' AS stem "
                "FROM tasks WHERE id = ANY($1::int[]) ORDER BY id",
                ids,
            )
            by_id = {r["id"]: r for r in rows}
            if len(by_id) != len(TARGETS):
                missing = set(ids) - set(by_id)
                raise RuntimeError(f"не найдены id: {missing}")

            print(f"Целевых заданий: {len(TARGETS)}")
            null_before_targets = sum(
                1 for r in rows if r["sr_is_null"]
            )
            print(f"Из них solution_rules=null: {null_before_targets} (ждём {len(TARGETS)})")
            if null_before_targets != len(TARGETS):
                already = [r["external_uid"] for r in rows if not r["sr_is_null"]]
                raise RuntimeError(
                    f"{len(TARGETS) - null_before_targets} заданий УЖЕ имеют solution_rules "
                    f"(возможно, скрипт уже применялся): {already}"
                )

            # ---- solution_rules UPDATE ----
            payloads: dict[int, str] = {}
            print("\nПримеры (id, external_uid, value, file_gated):")
            for tid, uid, value, file_gated in TARGETS:
                row = by_id[tid]
                payload = build_payload(value, row["max_score"], tid in MULTI_VALUE_IDS)
                payloads[tid] = json.dumps(payload)
            for tid, uid, value, file_gated in TARGETS[:10]:
                tag = "FILE-GATED" if file_gated else "solvable"
                print(f"  id={tid} {uid:16} value='{value}' [{tag}]")
            print(f"  ... и ещё {len(TARGETS) - 10}")

            updated = 0
            for tid, pj in payloads.items():
                res = await conn.execute(
                    "UPDATE tasks SET solution_rules = $2::jsonb "
                    "WHERE id = $1 AND (solution_rules IS NULL OR jsonb_typeof(solution_rules)='null')",
                    tid, pj,
                )
                updated += int(res.split()[-1])
            if updated != len(TARGETS):
                raise AssertionError(f"ожидали обновить {len(TARGETS)}, обновлено {updated}")

            # ---- <img> вставка (3 задания) ----
            img_updated = 0
            print("\nВставка <img> (3 задания):")
            for tid, anchor, sha_ext in IMAGES:
                stem = by_id[tid]["stem"]
                cnt = stem.count(anchor)
                if cnt != 1:
                    raise RuntimeError(f"id={tid}: анкер встречается {cnt} раз (нужно 1): {anchor!r}")
                if "<img" in stem:
                    raise RuntimeError(f"id={tid}: stem уже содержит <img> — не дублировать")
                img_tag = f'<img src="{MEDIA_BASE}/{sha_ext}"/><br>'
                new_stem = stem.replace(anchor, anchor + img_tag, 1)
                await conn.execute(
                    "UPDATE tasks SET task_content = jsonb_set(task_content, '{stem}', to_jsonb($2::text)) "
                    "WHERE id = $1",
                    tid, new_stem,
                )
                check = await conn.fetchval("SELECT task_content->>'stem' FROM tasks WHERE id=$1", tid)
                if sha_ext not in check:
                    raise AssertionError(f"id={tid}: <img> не подтвердился после записи")
                img_updated += 1
                print(f"  OK id={tid} sha={sha_ext[:12]}...")

            # ---- Верификация ----
            still_null = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE id = ANY($1::int[]) "
                "AND (solution_rules IS NULL OR jsonb_typeof(solution_rules)='null')",
                ids,
            )
            if still_null != 0:
                raise AssertionError(f"после записи остались null: {still_null}")

            accepted_ok = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE id = ANY($1::int[]) "
                "AND jsonb_array_length(COALESCE(solution_rules#>'{short_answer,accepted_answers}','[]'::jsonb)) = 1",
                ids,
            )
            if accepted_ok != len(TARGETS):
                raise AssertionError(f"accepted_answers mismatch: {accepted_ok} != {len(TARGETS)}")

            img_ok = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE id = ANY($1::int[]) AND task_content->>'stem' LIKE '%/api/v1/media/%'",
                [t[0] for t in IMAGES],
            )
            if img_ok != len(IMAGES):
                raise AssertionError(f"img mismatch: {img_ok} != {len(IMAGES)}")

            crylov_null_remaining = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE external_uid LIKE 'crylov:%' "
                "AND (solution_rules IS NULL OR jsonb_typeof(solution_rules)='null')"
            )
            print(f"\nOK: solution_rules записаны ({updated}/36), картинки вставлены ({img_updated}/3).")
            print(f"Крылов-заданий с solution_rules=null ПОСЛЕ (весь LMS): {crylov_null_remaining} (ждём 0)")
            if crylov_null_remaining != 0:
                raise AssertionError("остались null Крылов-задания вне выборки — проверь охват")

            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (запусти с --apply при DBCHECK_OK=1)")
        print("\nЗАПИСАНО И ЗАКОММИЧЕНО.")
    finally:
        await conn.close()


if __name__ == "__main__":
    try:
        asyncio.run(main("--apply" in sys.argv))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
