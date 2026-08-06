# -*- coding: utf-8 -*-
"""tsk-324 (продолжение): видео-подсказки Крылова -> task_content.hints_video.

ПОЧЕМУ ОТДЕЛЬНЫЙ СКРИПТ, НЕ РАСШИРЕНИЕ tsk324_apply_video_hints.py
kompege/sdamgia/polyakov джойнятся по единому числовому ID, зашитому в
заголовок VK-видео ("Разбираем задание №N (N_ID)") -- это позволяло построить
маппинг программно (regexp по title). У Крылова номер варианта в UID LMS
(`crylov:v{V}t{N}`), а в VK видео он вообще НЕ в заголовке (заголовок голый:
"Разбираем задание №N (Крылов)" без варианта) -- вариант живёт только в
ОПИСАНИИ видео, и там пять РАЗНЫХ шаблонов текста ("N Крылов С.С. 2026
Вариант V", "N Сборник Крылова С.С. вариант V", "N_Крылов С.С. Вариант V",
"Задание N Вариант Крылова С.С. V", "Задание  N_vV (Сборник Крылова С.С.
2026)", и один случай "18_1" вовсе без слова "вариант"). Одна общая регулярка
на 34 видео дала бы либо пропуски, либо ложные пары -- при таком объёме
(N=34) дешевле и безопаснее разобрать каждое видео вручную, сверяя ПОЛНЫЙ
текст условия video.description <-> tasks.task_content->>'stem', а не только
номер. Метод и прецедент -- `backfill_python_hints_video_tsk316.py`.

ЧТО ПРОВЕРЕНО ПЕРЕД ЗАПИСЬЮ (2026-08-06)
- Прочитаны ВСЕ 34 видео с "Крылов" в raw->>'description' (content_backbone
  prod, source_system='vk_importer').
- Для каждого извлечена пара (номер задания N, вариант V) из текста описания.
- Сверено с LMS: `crylov:v{V}t{N}` -- 18 из 34 видео относятся к заданиям, у
  которых hints_video УЖЕ заполнен (в т.ч. crylov:v11t26 = id 4585, тот самый
  "спорный ответ" из tsk-368) -- предыдущие проходы (tsk-317/319/355/367/381/
  382) их уже закрыли. Эти видео НЕ трогаем (WHERE hints_video пуст защищает
  и так, но эффект понятен заранее).
- 2 видео (v1t26, v5t24 x2) относятся к НЕАКТИВНЫМ заданиям -- не трогаем
  (is_active в WHERE).
- Оставшиеся 16 -- ПОЛНЫЙ текст stem сверен с ПОЛНЫМ текстом video.description
  построчно (не только номер/вариант). Два случая с текстовым расхождением в
  ОДНОЙ вводной фразе при полном совпадении условия и чисел -- отнесены к
  классу "мусор OCR/скана" (F9 плейбука), не к разным задачам:
    - crylov:v11t2: формула в stem "(¬w → ¬z)", в video.description
      "(-w -> -y)" -- единственное расхождение, всё остальное (Миша, три
      строки таблицы, переменные w,x,y,z, формат ответа) совпадает дословно.
    - crylov:v1t23: video.description говорит "три команды... латинскими
      буквами", stem -- "две команды... номерами" -- но сам список команд
      (Прибавь 1 / Поменяй местами), правило и числа 101->154 идентичны.

Результат (16 задач, все is_active, все hints_video были пусты):
crylov:v1t7, v1t11, v1t12, v1t15, v1t17, v1t18, v1t23, v1t24, v1t25, v1t27,
v5t26, v11t2, v11t11, v16t1, v16t5, v16t26.

Запуск: dry-run по умолчанию (транзакция откатывается); --apply -- запись
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

# external_uid ("crylov:v{V}t{N}") -> прямая VK-ссылка на видео-разбор.
# Каждая пара сверена вручную: video.description (content_backbone,
# vk_importer) <-> tasks.task_content->>'stem' (LMS), полный текст, не
# только номер. См. докстринг выше.
MAPPING: dict[str, str] = {
    "crylov:v1t7": "https://vk.com/video-53400615_456239476",
    "crylov:v1t11": "https://vk.com/video-53400615_456239477",
    "crylov:v1t12": "https://vk.com/video-53400615_456239478",
    "crylov:v1t15": "https://vk.com/video-53400615_456239449",
    "crylov:v1t17": "https://vk.com/video-53400615_456239470",
    "crylov:v1t18": "https://vk.com/video-53400615_456239484",
    "crylov:v1t23": "https://vk.com/video-53400615_456239471",
    "crylov:v1t24": "https://vk.com/video-53400615_456239472",
    "crylov:v1t25": "https://vk.com/video-53400615_456239473",
    "crylov:v1t27": "https://vk.com/video-53400615_456239474",
    "crylov:v5t26": "https://vk.com/video-53400615_456239499",
    "crylov:v11t2": "https://vk.com/video-53400615_456240294",
    "crylov:v11t11": "https://vk.com/video-53400615_456240298",
    "crylov:v16t1": "https://vk.com/video-53400615_456240335",
    "crylov:v16t5": "https://vk.com/video-53400615_456240337",
    "crylov:v16t26": "https://vk.com/video-53400615_456240354",
}

# Идемпотентно (в отличие от tsk316: там владели полем целиком и допускали
# апгрейд, здесь -- как в tsk324_apply_video_hints -- пишем ТОЛЬКО в пустое
# поле, WHERE-гвард перепроверяется и на самой записи).
UPDATE_ONE = """
UPDATE tasks
SET task_content = task_content || jsonb_build_object(
        'hints_video', $2::jsonb,
        'has_hints', true
    )
WHERE external_uid = $1
  AND is_active
  AND jsonb_array_length(COALESCE(task_content->'hints_video', '[]'::jsonb)) = 0
"""


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
        raise RuntimeError("Не нашёл прод-DSN learn (5.42.107.253/learn). Передай LEARN_PROD_DSN явно.")
    return dsn


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    target_uids = list(MAPPING.keys())
    try:
        async with conn.transaction():
            before = {
                r["external_uid"]: r
                for r in await conn.fetch(
                    "SELECT id, external_uid, is_active, "
                    "jsonb_array_length(COALESCE(task_content->'hints_video','[]'::jsonb)) AS n_hints, "
                    "md5(COALESCE(task_content->>'stem','')) AS stem_md5, "
                    "md5(COALESCE(solution_rules::text,'')) AS solrules_md5 "
                    "FROM tasks WHERE external_uid = ANY($1::text[])",
                    target_uids,
                )
            }
            missing = [u for u in target_uids if u not in before]
            if missing:
                raise RuntimeError(f"нет в БД: {missing}")
            not_ready = [u for u, r in before.items() if not r["is_active"] or r["n_hints"] > 0]
            if not_ready:
                raise RuntimeError(f"уже не пусто/неактивно (маппинг устарел, перепроверь): {not_ready}")

            print(f"Целевых заданий: {len(target_uids)} (все is_active, hints_video пуст)")
            print("Примеры (external_uid -> видео):")
            for uid in target_uids[:5]:
                print(f"  {uid} (id={before[uid]['id']}) -> ['{MAPPING[uid]}']")

            updated = 0
            for uid, url in MAPPING.items():
                payload = json.dumps([url], ensure_ascii=False)
                res = await conn.execute(UPDATE_ONE, uid, payload)
                updated += int(res.split()[-1])
            print(f"\nUPDATE затронул строк: {updated} (ожидали {len(target_uids)})")
            if updated != len(target_uids):
                raise AssertionError(f"обновлено {updated} != {len(target_uids)} — расхождение состояния")

            after = {
                r["external_uid"]: r
                for r in await conn.fetch(
                    "SELECT external_uid, task_content->'hints_video' AS hv, "
                    "(task_content->>'has_hints')::bool AS has_hints, "
                    "md5(COALESCE(task_content->>'stem','')) AS stem_md5, "
                    "md5(COALESCE(solution_rules::text,'')) AS solrules_md5 "
                    "FROM tasks WHERE external_uid = ANY($1::text[])",
                    target_uids,
                )
            }
            for uid, url in MAPPING.items():
                a = after[uid]
                hv = json.loads(a["hv"]) if a["hv"] else []
                if hv != [url]:
                    raise AssertionError(f"{uid}: hints_video={hv} != ['{url}']")
                if a["has_hints"] is not True:
                    raise AssertionError(f"{uid}: has_hints={a['has_hints']} != true")
                if a["stem_md5"] != before[uid]["stem_md5"]:
                    raise AssertionError(f"{uid}: stem ИЗМЕНЁН — недопустимо")
                if a["solrules_md5"] != before[uid]["solrules_md5"]:
                    raise AssertionError(f"{uid}: solution_rules ИЗМЕНЁН — недопустимо")

            print(f"Верификация: у всех {len(target_uids)} hints_video совпал с планом, has_hints=true, "
                  "stem и solution_rules не изменены.")
            print("\nOK: подсказки проставлены, коллатералей нет.")
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
