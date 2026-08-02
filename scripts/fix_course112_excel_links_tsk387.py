"""tsk-387 (довесок 2026-08-02) — материалы 318 (курс 138) и 444 (курс 160)
курса 112 ссылались на внешний сайт victor-komlev.ru за темами, которые
после переноса 161/164 (fix_course112_excel_reparent_tsk387.py) уже есть
внутри самого дерева курса 112. Платформа не умеет глубоких внутренних
ссылок на конкретную подтему (`/courses/id-<N>` открывает КОРЕНЬ курса 112,
не конкретный узел — проверено живым прогоном), поэтому фикс — снять
внешнюю ссылку (текст остаётся) и добавить короткую пометку, что тема уже
разобрана в подкурсах курса, а не отправлять ученика на сайт за уже
пройденным контентом.

Проверено read-only ДО правки (SQL-грep по всему дереву курса 112 на
`href="https?://(www.)?victor-komlev.ru[^"]*"`, исключая `/wp-content/uploads/`
— это медиа-файлы, законный внешний хостинг картинок/шаблонов, не тот
паттерн): единственные ДВА материала, где внешняя ссылка ведёт на статью,
дублирующую контент, который теперь физически есть в дереве курса 112 —
318 (7 ссылок на 161/164) и 444 (2 ссылки на те же 161/164). Остальные
найденные внешние ссылки (regulyarnye-vyrazheniya-v-python, tsikly-v-python,
rabota-so-strokami-v-python и т.п.) — общие Python-статьи без LMS-дубликата
внутри дерева курса 112, за пределы этой правки не выходим.

Запуск (на прод-сервере, sudo -u app, .env с прод DSN):
    python scripts/fix_course112_excel_links_tsk387.py              # dry-run
    python scripts/fix_course112_excel_links_tsk387.py --apply       # COMMIT
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))
load_dotenv(project_root / ".env", encoding="utf-8-sig")

from sqlalchemy import text  # noqa: E402

from app.db.session import async_session_factory  # noqa: E402

MATERIAL_318_OLD_TEXT = (
    "Для решения задач по данной теме, нам понадобится умение работать с MS Excel:\r\n"
    "<ol>\n"
    "<li><a href=\"https://victor-komlev.ru/osnovy-raboty-v-elektronnyh-tablitsah-excel-i-google-sheets/\" rel=\"noopener\" target=\"_blank\">Таблицы в Excel</a></li>\n"
    "<li><a href=\"https://victor-komlev.ru/sortirovka-dannyh-v-excel/\" rel=\"noopener\" target=\"_blank\">Сортировка данных</a></li>\n"
    "<li><a href=\"https://victor-komlev.ru/kak-rabotat-s-filtrami-v-excel/\" rel=\"noopener\" target=\"_blank\">Фильтрация данных</a></li>\n"
    "<li><a href=\"https://victor-komlev.ru/formuly-i-funktsii-v-excel/#kak-vvesti-formulu-v-yacheyku\" rel=\"noopener\" target=\"_blank\">Умение строить формулы</a></li>\n"
    "<li><a href=\"https://victor-komlev.ru/formuly-i-funktsii-v-excel/#agregatnye-funktsii-v-excel-i-ih-ispolzovanie\" rel=\"noopener\" target=\"_blank\">Умение работать с агрегатными функциями</a></li>\n"
    "<li><a href=\"https://victor-komlev.ru/formuly-i-funktsii-v-excel/#funktsiya-vpr\" rel=\"noopener\" target=\"_blank\">Владение функцией ВПР</a></li>\n"
    "<li><a href=\"https://victor-komlev.ru/osnovy-raboty-v-elektronnyh-tablitsah-excel-i-google-sheets/\" rel=\"noopener\" target=\"_blank\">Умение строить сводные таблицы</a></li>\n"
    "<li>Понимание организации хранения данных в БД:\r\n"
    "<ol>\n"
    "<li>Таблицы, записи, поля</li>\n"
    "<li>Ключи</li>\n"
    "<li>Взаимосвязи между таблицами</li>\n"
    "</ol>\n"
    "</li>\n"
    "</ol>\n"
    "<blockquote class=\"warning\">Внимание! Начиная с учебного года 2025-2026, задания выполняются в <strong>LibreOffice</strong></blockquote>"
)

MATERIAL_318_NEW_TEXT = (
    "Для решения задач по данной теме, нам понадобится умение работать с MS Excel "
    "— пункты 1-7 уже разобраны в подкурсах «Основы работы в электронных таблицах» "
    "и «Формулы и функции в Excel» (идут прямо перед этой темой):\r\n"
    "<ol>\n"
    "<li>Таблицы в Excel</li>\n"
    "<li>Сортировка данных</li>\n"
    "<li>Фильтрация данных</li>\n"
    "<li>Умение строить формулы</li>\n"
    "<li>Умение работать с агрегатными функциями</li>\n"
    "<li>Владение функцией ВПР</li>\n"
    "<li>Умение строить сводные таблицы</li>\n"
    "<li>Понимание организации хранения данных в БД:\r\n"
    "<ol>\n"
    "<li>Таблицы, записи, поля</li>\n"
    "<li>Ключи</li>\n"
    "<li>Взаимосвязи между таблицами</li>\n"
    "</ol>\n"
    "</li>\n"
    "</ol>\n"
    "<blockquote class=\"warning\">Внимание! Начиная с учебного года 2025-2026, задания выполняются в <strong>LibreOffice</strong></blockquote>"
)

MATERIAL_444_OLD_TEXT = (
    "<ul>\n"
    "<li>Адреса ячеек, диапазоны (<code>A1</code>, <code>B2:C4</code>), формулы начинаются с <code>=</code>, базовые операторы <code>+ − * / ^</code>. </li>\n"
    "<li>Базовые агрегаты по диапазону: <code>МАКС()</code>, <code>МИН()</code>, <code>СРЗНАЧ()</code> (англ.: MAX/MIN/AVERAGE).</li>\n"
    "<li>Условия и подсчёты: <code>ЕСЛИ()</code>, <code>СЧЁТЕСЛИ()</code> (англ.: IF/COUNTIF).</li>\n"
    "<li>Порядковые статистики: <code>НАИМЕНЬШИЙ()</code>, <code>НАИБОЛЬШИЙ()</code>.</li>\n"
    "</ul>\n"
    "<h3>🔁 Что повторить в Excel</h3>\n"
    "<ul>\n"
    "<li><a href=\"https://victor-komlev.ru/formuly-i-funktsii-v-excel/\" rel=\"noopener\" target=\"_blank\">Формулы</a></li>\n"
    "<li><a href=\"https://victor-komlev.ru/osnovy-raboty-v-elektronnyh-tablitsah-excel-i-google-sheets/\" rel=\"noopener\" target=\"_blank\">Функции</a></li>\n"
    "</ul>"
)

MATERIAL_444_NEW_TEXT = (
    "<ul>\n"
    "<li>Адреса ячеек, диапазоны (<code>A1</code>, <code>B2:C4</code>), формулы начинаются с <code>=</code>, базовые операторы <code>+ − * / ^</code>. </li>\n"
    "<li>Базовые агрегаты по диапазону: <code>МАКС()</code>, <code>МИН()</code>, <code>СРЗНАЧ()</code> (англ.: MAX/MIN/AVERAGE).</li>\n"
    "<li>Условия и подсчёты: <code>ЕСЛИ()</code>, <code>СЧЁТЕСЛИ()</code> (англ.: IF/COUNTIF).</li>\n"
    "<li>Порядковые статистики: <code>НАИМЕНЬШИЙ()</code>, <code>НАИБОЛЬШИЙ()</code>.</li>\n"
    "</ul>\n"
    "<h3>🔁 Что повторить в Excel</h3>\n"
    "<p>Формулы и функции уже разобраны в подкурсах «Формулы и функции в Excel» "
    "и «Основы работы в электронных таблицах» — они изучаются раньше, в теме "
    "«Задание 3».</p>"
)


async def main(apply: bool) -> int:
    mode = "APPLY (COMMIT)" if apply else "DRY-RUN (ROLLBACK)"
    print(f"=== tsk-387 (довесок): убрать внешние ссылки из материалов 318/444 — {mode} ===")

    async with async_session_factory() as db:
        try:
            for material_id, old_text, new_text in (
                (318, MATERIAL_318_OLD_TEXT, MATERIAL_318_NEW_TEXT),
                (444, MATERIAL_444_OLD_TEXT, MATERIAL_444_NEW_TEXT),
            ):
                row = (await db.execute(text(
                    "SELECT content->>'text' AS t FROM materials WHERE id=:id"
                ), {"id": material_id})).mappings().first()
                current = row["t"] if row else None
                if current != old_text:
                    raise AssertionError(
                        f"material {material_id}: текущий текст не совпадает с ожидаемым "
                        f"BEFORE — правка устарела или уже применена, СТОП без записи"
                    )
                print(f"material {material_id}: BEFORE-текст совпал с ожидаемым — OK")

                result = await db.execute(text(
                    "UPDATE materials SET content = jsonb_set(content, '{text}', to_jsonb(cast(:new_text as text))) "
                    "WHERE id=:id"
                ), {"id": material_id, "new_text": new_text})
                assert result.rowcount == 1, f"material {material_id}: обновлено {result.rowcount} строк, ожидали 1"

                after = (await db.execute(text(
                    "SELECT content->>'text' AS t FROM materials WHERE id=:id"
                ), {"id": material_id})).mappings().first()
                assert after["t"] == new_text, f"material {material_id}: AFTER-текст не совпал с ожидаемым"
                assert "victor-komlev.ru" not in after["t"], f"material {material_id}: внешняя ссылка всё ещё в тексте"
                print(f"material {material_id}: AFTER-текст обновлён, внешних ссылок на victor-komlev.ru не осталось — OK")

        except Exception as exc:  # noqa: BLE001
            print(f"\nОШИБКА: {exc!r} — ROLLBACK")
            await db.rollback()
            return 1

        if apply:
            await db.commit()
            print("\nCOMMIT — правки применены и закоммичены.")
        else:
            await db.rollback()
            print("\nROLLBACK — dry-run, изменения откатаны.")

    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Выполнить COMMIT.")
    args = ap.parse_args()
    sys.exit(asyncio.run(main(apply=args.apply)))
