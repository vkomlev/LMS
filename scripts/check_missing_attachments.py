# -*- coding: utf-8 -*-
"""Регулярный чек: активные задания, где условие требует файл, а файла нет (tsk-369).

Зачем. Задания ЕГЭ №3, 9, 10, 17, 18, 22, 24, 26, 27 формулируются коротко («Откройте
файл электронной таблицы…», «Текстовый файл состоит из…»), а всё различие между задачами
одного типа — в приложенном файле. Нет файла — решить задачу невозможно в принципе,
сколько ни думай. Наружу это не всплывает ничем: у задания есть и текст, и правило
проверки, оно формально исправно (в отличие от класса tsk-361 — там пустое правило).
На 2026-07-22 таких заданий на проде было 224 из 340 с файловым условием.

Почему чек именно такой.

1. **Мягкие переносы.** sdamgia расставляет U+00AD внутри слов («Тек­сто­вый файл
   со­сто­ит»). Без их снятия поиск теряет целую партию: первичный разбор насчитал
   108 заданий вместо 224 ровно поэтому.

2. **«Файл есть» = ссылка в условии.** Ученику файл виден только ссылкой `/api/v1/media/`
   внутри `stem`: клиент SPW рисует условие как HTML и поле `attached_file_paths` не
   читает. Поэтому непустой `attached_file_paths` сам по себе НЕ считается наличием
   файла — он лишь машинный учёт импорта ContentBackbone, и такое задание попадёт
   в находки со специальной пометкой.

3. **`task_content.media` не считается вовсе.** Это поле пусто у всех 6303 активных
   заданий (`[]` либо отсутствует) — механизм не используется ни одним импортом.
   Проверка «media пуст» ловила бы всё подряд и не значила бы ничего.

Что делает. Считает и перечисляет активные задания с файловым условием без ссылки на файл.
Read-only: ни одного UPDATE. Чинит не этот скрипт — он только сообщает.

Куда смотрит. В базу из `DATABASE_URL`; по умолчанию это dev (прод от скриптов закрыт,
tsk-246). Прод — явным override:
    DATABASE_URL=<прод-dsn> python scripts/check_missing_attachments.py
Скрипт всегда печатает хост и базу, которую проверил.

Запуск из корня проекта:
    python scripts/check_missing_attachments.py            # полный отчёт
    python scripts/check_missing_attachments.py --quiet     # только находки (для планировщика)

Коды выхода: 0 — таких заданий нет; 1 — найдены; 2 — ошибка выполнения.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(dotenv_path=project_root / ".env", encoding="utf-8-sig")

# Формулировки, которые означают «данные задачи лежат в приложенном файле».
# Список закрытый и намеренно консервативный: лучше пропустить редкую формулировку,
# чем каждую неделю разбирать ложные находки и перестать читать отчёт.
FILE_GATE_RE = (
    r"(откройте файл|откройте прилага|в файле содерж|в файле привед|в файле, содерж|"
    r"прилагаемом файле|прилагается файл|входного файла|текстовый файл состоит|"
    r"файл электронной таблиц|в прикреплённом файле|в прикрепленном файле|"
    r"данные для выполнения|откройте один из файлов|с помощью текстового редактора|"
    r"в текстовом файле)"
)

SQL_MISSING_ATTACHMENT = f"""
SELECT t.id, t.course_id, t.external_uid,
       t.task_content->>'type' AS task_type,
       coalesce(jsonb_array_length(t.task_content->'attached_file_paths'), 0) AS meta_paths,
       left(replace(regexp_replace(t.task_content->>'stem', '<[^>]+>', ' ', 'g'), chr(173), ''), 70) AS stem
FROM tasks t
WHERE t.is_active
  AND lower(replace(regexp_replace(t.task_content->>'stem', '<[^>]+>', ' ', 'g'), chr(173), ''))
      ~ '{FILE_GATE_RE}'
  AND (t.task_content->>'stem') NOT LIKE '%/api/v1/media/%'
ORDER BY t.course_id, t.id
"""


async def main(quiet: bool) -> int:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        print("ОШИБКА: не задан DATABASE_URL (ни в окружении, ни в .env)", file=sys.stderr)
        return 2
    if dsn.startswith("postgresql://"):
        dsn = dsn.replace("postgresql://", "postgresql+asyncpg://", 1)

    engine = create_async_engine(dsn, echo=False)
    try:
        async with engine.connect() as conn:
            where = (await conn.execute(text(
                "SELECT current_database() AS db, inet_server_addr()::text AS host"
            ))).mappings().first()
            print(f"Проверяю базу: {where['db']} на {where['host'] or 'localhost'}")
            rows = (await conn.execute(text(SQL_MISSING_ATTACHMENT))).mappings().all()
    finally:
        await engine.dispose()

    if not rows:
        if not quiet:
            print("\nOK: у всех заданий с файловым условием файл на месте.")
        return 0

    print(f"\nУСЛОВИЕ ТРЕБУЕТ ФАЙЛ, А ССЫЛКИ НА ФАЙЛ НЕТ: {len(rows)}")
    by_course: dict[int, int] = {}
    for r in rows:
        by_course[r["course_id"]] = by_course.get(r["course_id"], 0) + 1
    for course_id, n in sorted(by_course.items(), key=lambda kv: -kv[1]):
        print(f"  курс {course_id}: {n}")

    # Отдельная подсветка: метаданные о файле есть, а ссылки в условии нет — значит
    # файл в хранилище лежит, но ученик его не увидит. Чинится дописыванием ссылки.
    meta_only = [r for r in rows if r["meta_paths"] > 0]
    if meta_only:
        print(f"\n  из них файл ЕСТЬ в метаданных, но не виден ученику: {len(meta_only)}")
        print("  id: " + ", ".join(str(r["id"]) for r in meta_only[:30]))

    print("  примеры: " + ", ".join(str(r["id"]) for r in rows[:15]))
    print(f"\nИТОГО нерешаемых из-за отсутствия файла: {len(rows)}")
    return 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true", help="печатать только находки")
    args = ap.parse_args()
    try:
        sys.exit(asyncio.run(main(quiet=args.quiet)))
    except Exception as exc:  # noqa: BLE001 — чек под планировщиком, причина обязана попасть в лог
        print(f"ОШИБКА выполнения чека: {exc}", file=sys.stderr)
        sys.exit(2)
