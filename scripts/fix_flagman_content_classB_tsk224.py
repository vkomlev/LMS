"""tsk-224 (класс B, флагман «AI-предприниматель»): контентные дефекты фидбека преподавателя.

Находки (сверены на проде learn_prod_db, дерево 1064→1081/1086):
  5. Дубль контента: материал 2230 «Компьютер и файлы» — ASCII-таблица даёт Файл/Папка,
     а <ul> ниже их повторяет. Убираем дубль из <ul>, оставляем новое (Путь, Терминал).
  6. Повтор мысли «не зубри»: 2229 «Словарь новичка» уже говорит «не нужно заучивать
     наизусть»; хвост ASCII в 2230 повторяет «Не зубри: возвращайся к словарю» — срезаем в 2230.
  7. Слабые вопросы (задания 6314/6315, курс 1082): оба проверяли одну мысль, ответы
     дословно повторяли буллеты теории 2220, дистракторы абсурдны. Переписаны на применение
     (сценарий / понимание сдвига рынка) с правдоподобными дистракторами. Верный ответ
     остаётся "a" → solution_rules НЕ меняется.
  8. MCP «бегло» (материал 2272, курс 1093): обзор говорит «подключаешь готовый MCP-сервер»,
     но не разъясняет, что «подключить» ≠ «написать» → путаница «как создавать? написать
     Клоду?». Добавляем короткий абзац: свои серверы писать не нужно, подключение = запись
     в настройки/командой.

Запуск: dry-run по умолчанию; --apply (нужен DBCHECK_OK=1).
"""
import asyncio
import json
import os
import sys

import asyncpg
from dotenv import load_dotenv

# --- находки 5+6: материал 2230 ---
MAT_2230 = 2230
DUP_UL = "<li><b>Файл</b> — документ на компьютере. <b>Папка</b> — коробка с файлами. <b>Путь</b> — адрес, где лежит файл.</li>"
NEW_UL = "<li><b>Путь</b> — адрес, где лежит файл: по нему компьютер находит нужный документ среди папок.</li>"
NE_ZUBRI_2230 = "\n\nНе зубри: возвращайся к словарю, когда слово встретится в практике."

# --- находка 8: материал 2272 ---
MAT_2272 = 2272
MCP_CLARIFY = (
    "\n<p><b>«Подключить» — это не «написать».</b> Свои MCP-серверы писать не нужно: "
    "для популярных сервисов (Google Sheets, GitHub, Telegram) уже есть готовые. "
    "Подключение — это один раз прописать нужный сервер в настройках Claude (или добавить "
    "командой), после чего Claude сам умеет с ним работать. Писать собственный MCP-сервер — "
    "редкий продвинутый случай, в курсе он не понадобится.</p>"
)
MCP_MARKER = "«Подключить» — это не «написать»."

# --- находка 7: задания 6314 / 6315 ---
TASK_6314 = 6314
STEM_6314_OLD = "Какая мысль — главная в роли «AI-предприниматель»?"
STEM_6314_NEW = (
    "Двое одинаково знают Python. Первый берёт заказы и зарабатывает, второй сидит без работы. "
    "Что, скорее всего, отличает первого?"
)
OPTS_6314 = [
    {"id": "a", "text": "Он берётся за проблему клиента и доводит её до решения, а код для него — лишь инструмент", "scores": None, "is_active": True, "explanation": None},
    {"id": "b", "text": "Он выучил больше языков программирования", "scores": None, "is_active": True, "explanation": None},
    {"id": "c", "text": "Он быстрее печатает и пишет больше строк кода", "scores": None, "is_active": True, "explanation": None},
]

TASK_6315 = 6315
STEM_6315_OLD = "Почему «просто знать Python» сегодня мало?"
STEM_6315_NEW = "Почему сегодня мало просто уметь писать код на Python?"
OPTS_6315 = [
    {"id": "a", "text": "Рутинный код всё лучше пишет AI — ценится тот, кто понимает задачу и собирает из кода рабочее решение", "scores": None, "is_active": True, "explanation": None},
    {"id": "b", "text": "Python устарел, на нём почти не пишут", "scores": None, "is_active": True, "explanation": None},
    {"id": "c", "text": "Заказчику важно количество строк кода, а не результат", "scores": None, "is_active": True, "explanation": None},
]


def _dsn() -> str:
    load_dotenv(".env", encoding="utf-8-sig", override=False)
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


async def _fix_material_2230(conn) -> None:
    text = await conn.fetchval("SELECT content->>'text' FROM materials WHERE id=$1", MAT_2230)
    if text is None:
        raise RuntimeError(f"материал {MAT_2230}: не найден")
    if DUP_UL not in text or NE_ZUBRI_2230 not in text:
        raise RuntimeError(f"материал {MAT_2230}: якоря не найдены — возможно уже правлено")
    new = text.replace(NE_ZUBRI_2230, "").replace(DUP_UL, NEW_UL)
    assert "Не зубри" not in new, "«Не зубри» не срезано"
    assert "документ на компьютере" not in new, "дубль <ul> не убран"
    assert NEW_UL in new, "новый <ul> не вставлен"
    assert "секретный пропуск" in new, "ASCII-таблица потеряна"
    await conn.execute(
        "UPDATE materials SET content = jsonb_set(content,'{text}', to_jsonb($2::text)) WHERE id=$1",
        MAT_2230, new,
    )
    check = await conn.fetchval("SELECT content->>'text' FROM materials WHERE id=$1", MAT_2230)
    assert check == new, "материал 2230 не применился"
    print(f"OK материал {MAT_2230}: дубль <ul> убран, «Не зубри» срезано (находки 5+6)")


async def _fix_material_2272(conn) -> None:
    text = await conn.fetchval("SELECT content->>'text' FROM materials WHERE id=$1", MAT_2272)
    if text is None:
        raise RuntimeError(f"материал {MAT_2272}: не найден")
    if MCP_MARKER in text:
        raise RuntimeError(f"материал {MAT_2272}: разъяснение уже добавлено")
    if not text.rstrip().endswith("</pre>"):
        raise RuntimeError(f"материал {MAT_2272}: ожидал конец на </pre>, структура иная")
    new = text + MCP_CLARIFY
    assert MCP_MARKER in new and "готовые" in new, "разъяснение MCP не вставлено"
    await conn.execute(
        "UPDATE materials SET content = jsonb_set(content,'{text}', to_jsonb($2::text)) WHERE id=$1",
        MAT_2272, new,
    )
    check = await conn.fetchval("SELECT content->>'text' FROM materials WHERE id=$1", MAT_2272)
    assert check == new, "материал 2272 не применился"
    print(f"OK материал {MAT_2272}: добавлено разъяснение 'подключить - не написать' (находка 8)")


async def _fix_task(conn, task_id, stem_old, stem_new, opts_new) -> None:
    row = await conn.fetchrow(
        "SELECT task_content->>'stem' AS stem, solution_rules->'correct_options' AS correct FROM tasks WHERE id=$1",
        task_id,
    )
    if row is None:
        raise RuntimeError(f"задание {task_id}: не найдено")
    if row["stem"].strip() != stem_old:
        raise RuntimeError(f"задание {task_id}: stem не совпал с ожидаемым — возможно уже правлено.\n  есть: {row['stem']!r}")
    correct = json.loads(row["correct"]) if row["correct"] else []
    if correct != ["a"]:
        raise RuntimeError(f"задание {task_id}: correct_options={correct}, ожидал ['a'] — стоп")
    await conn.execute(
        "UPDATE tasks SET task_content = jsonb_set(jsonb_set(task_content,'{stem}', to_jsonb($2::text)),'{options}', $3::jsonb) WHERE id=$1",
        task_id, stem_new, json.dumps(opts_new, ensure_ascii=False),
    )
    chk = await conn.fetchrow(
        "SELECT task_content->>'stem' AS stem, task_content->'options'->0->>'text' AS opt_a, solution_rules->'correct_options' AS correct FROM tasks WHERE id=$1",
        task_id,
    )
    assert chk["stem"] == stem_new, "stem не применился"
    assert chk["opt_a"] == opts_new[0]["text"], "опция a не применилась"
    assert json.loads(chk["correct"]) == ["a"], "correct_options изменился неожиданно"
    print(f"OK задание {task_id}: stem+опции переписаны, верный ответ по-прежнему 'a' (находка 7)")
    print(f"  новый stem: {chk['stem']}")


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            await _fix_material_2230(conn)
            await _fix_material_2272(conn)
            await _fix_task(conn, TASK_6314, STEM_6314_OLD, STEM_6314_NEW, OPTS_6314)
            await _fix_task(conn, TASK_6315, STEM_6315_OLD, STEM_6315_NEW, OPTS_6315)
            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (запусти с --apply)")
        print("\nЗАПИСАНО И ЗАКОММИЧЕНО.")
    finally:
        await conn.close()


if __name__ == "__main__":
    apply = "--apply" in sys.argv
    try:
        asyncio.run(main(apply))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
