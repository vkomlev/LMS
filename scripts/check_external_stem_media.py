# -*- coding: utf-8 -*-
"""Регулярный чек: картинки заданий и материалов, которые браузер ученика не покажет (tsk-759).

Зачем. Боевой сайт отдаёт заголовок политики безопасности:

    img-src 'self' data: https://api.learn.victor-komlev.ru https://s3.twcstorage.ru
            https://victor-komlev.ru https://www.victor-komlev.ru

(источник истины — `SPW/next.config.ts`, директива `img-src`). Картинка с любого
другого адреса блокируется браузером ДО запроса: файл на чужом сайте жив, сервер
отдаёт 200, в логах пусто — а у ученика на месте схемы пустая рамка.

Дефект невидим снаружи (плейбук импорта, §11.3): у задания есть и текст, и правило
проверки, оно формально исправно. 01.09.2026 так стояли 37 активных заданий в 5
курсах, из них 23 нерешаемых в принципе — всё условие (схема дорог + таблица длин)
жило только в картинке. Нашлось не чеком, а живым учеником, который застрял.

Почему такие задания появляются. Партия `wp_nav:*` заезжала мимо докачки медиа:
`CB/monolith/external_tasks/wp_nav_import.py::build_task_upsert_item` кладёт stem
источника как есть, а `media/enricher.py::enrich_with_cas` (он и переписывает
`<img src>` на `/api/v1/media/<sha>`) вызывается только из runner-пути обычного
конвейера. Отсюда и наблюдаемый контраст: соседние задания `ext:d4` — с нашей
картинкой, `wp_nav` — с чужой.

Что делает. Перечисляет активные задания и материалы, где `<img src>` ведёт на адрес
вне списка разрешённых. Read-only: ни одного UPDATE. Чинит не этот скрипт — перенос
файла к себе делают `CB/scripts/tsk759_external_images_to_cas.py` (шаг 1, кладёт в
CAS + S3 и проверяет отдачу) и `scripts/tsk759_rewrite_external_images.py` (шаг 2,
переписывает ссылку в stem).

Куда смотрит. В базу из `DATABASE_URL`; по умолчанию это dev (прод от скриптов
закрыт, tsk-246). Прод — явным override:
    DATABASE_URL=<прод-dsn> python scripts/check_external_stem_media.py
Скрипт всегда печатает хост и базу, которую проверил.

Запуск из корня проекта:
    python scripts/check_external_stem_media.py            # полный отчёт
    python scripts/check_external_stem_media.py --quiet     # только находки

Под планировщиком чек идёт через общий вход ``scripts/weekly_checks.py external-media``
(журнал — ``logs/external_media_check.log``).

Коды выхода: 0 — таких картинок нет; 1 — найдены; 2 — ошибка выполнения.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

# Под pythonw консоли нет; `os.system` поднял бы отдельное окно cmd.exe (tsk-641).
if sys.platform == "win32" and not os.environ.get("LMS_CHECK_NO_CONSOLE"):
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(dotenv_path=project_root / ".env", encoding="utf-8-sig")

# Хосты из директивы `img-src` боевого CSP. Меняются вместе с `SPW/next.config.ts` —
# список короткий и меняется раз в год, поэтому дублируется здесь явно, а не
# вычитывается из чужого репозитория (иначе чек ломался бы от переезда SPW).
ALLOWED_HOSTS = (
    "api.learn.victor-komlev.ru",
    "s3.twcstorage.ru",
    "victor-komlev.ru",
    "www.victor-komlev.ru",
)
_HOSTS_SQL = ", ".join(f"'{h}'" for h in ALLOWED_HOSTS)

# Относительные ссылки и data: URL под политику подпадают как 'self'/data: — их не берём.
SQL_TASKS = f"""
WITH imgs AS (
    SELECT t.id, t.course_id, t.external_uid, m[1] AS url
    FROM tasks t,
         LATERAL regexp_matches(coalesce(t.task_content->>'stem', ''),
                                '<img[^>]+src="([^"]+)"', 'g') AS m
    WHERE t.is_active
)
SELECT id, course_id, external_uid, url,
       substring(url from '^https?://([^/]+)') AS host
FROM imgs
WHERE url ~ '^https?://'
  AND substring(url from '^https?://([^/]+)') NOT IN ({_HOSTS_SQL})
ORDER BY course_id, id
"""

SQL_MATERIALS = f"""
WITH imgs AS (
    SELECT mt.id, mt.course_id, mt.external_uid, m[1] AS url
    FROM materials mt,
         LATERAL regexp_matches(coalesce(mt.content::text, ''),
                                '<img[^>]+src=\\"([^\\"]+)\\"', 'g') AS m
    WHERE mt.is_active
)
SELECT id, course_id, external_uid, url,
       substring(url from '^https?://([^/]+)') AS host
FROM imgs
WHERE url ~ '^https?://'
  AND substring(url from '^https?://([^/]+)') NOT IN ({_HOSTS_SQL})
ORDER BY course_id, id
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
            if not quiet:
                print(f"Проверяю базу: {where['db']} на {where['host'] or 'localhost'}")
            task_rows = (await conn.execute(text(SQL_TASKS))).mappings().all()
            material_rows = (await conn.execute(text(SQL_MATERIALS))).mappings().all()
    finally:
        await engine.dispose()

    if not task_rows and not material_rows:
        if not quiet:
            print("\nOK: все картинки заданий и материалов лежат по разрешённым адресам.")
        return 0

    for label, rows, fix in (
        ("ЗАДАНИЯ", task_rows,
         "  Чинить: CB scripts/tsk759_external_images_to_cas.py (шаг 1) → "
         "LMS scripts/tsk759_rewrite_external_images.py (шаг 2)."),
        ("МАТЕРИАЛЫ", material_rows,
         "  Чинить: тот же перенос в CAS, ссылку в content правит публикатор материала."),
    ):
        if not rows:
            continue
        ids = sorted({r["id"] for r in rows})
        print(f"\n{label}: картинка на чужом адресе — браузер её не покажет "
              f"({len(ids)} шт., ссылок {len(rows)})")
        by_host: dict[str, int] = {}
        for r in rows:
            by_host[r["host"]] = by_host.get(r["host"], 0) + 1
        for host, n in sorted(by_host.items(), key=lambda kv: -kv[1]):
            print(f"  {host}: ссылок {n}")
        print("  id: " + ", ".join(str(i) for i in ids[:30])
              + (" …" if len(ids) > 30 else ""))
        print(fix)

    total = len({r["id"] for r in task_rows}) + len({r["id"] for r in material_rows})
    print(f"\nИТОГО с невидимой картинкой: {total}")
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
