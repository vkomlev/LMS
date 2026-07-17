"""tsk-261 (класс B7, остаток): развернуть битые относительные ссылки в текст.

После срезки WP-навигации (strip_wp_nav_block) в 40 материалах остались смысловые
перекрёстные ссылки — «Словарь бот-мейкера», «прошлом уроке», «системный блок»,
«2.5 Типы данных». Все — относительные WP-пути (`/sozdanie-chat-botov-navigator-…`,
`/vstupitelnye-it-vuz-navigator-…`), которые в SPW ведут в **404** (проверено: WP=200,
learn.=404). Их адресат — соседний урок того же курса, он достижим через дерево курса.

РЕШЕНИЕ (по принципу оператора «не выгонять ученика из LMS на WP»): не абсолютизируем на
WordPress, а **разворачиваем битую ссылку в текст** — фраза-подсказка остаётся («см. в
прошлом уроке»), битый линк исчезает. Тот же безопасный приём, что для источника-ссылки.
Где адресат — переиспользуемый узел (ОГЭ «Что нужно знать» → теория, уже подключённая
подкурсом), связь сохраняется через дерево; подключение подкурсом там, где его ещё нет —
follow-up tsk-263.

Разворачиваем ТОЛЬКО `<a>` с относительным href на WP-путь (`href="/[буква]"`). Абсолютные
ссылки (`http…`), внутриприложенческие и якоря не трогаем.

Запуск: dry-run по умолчанию; --apply (нужен DBCHECK_OK=1).
"""
import asyncio
import os
import re
import sys

import asyncpg
from dotenv import load_dotenv

# <a ... href="/что-то" ...>текст</a> → текст. Только относительный WP-путь (/буква).
REL_LINK_RE = re.compile(r'<a\b[^>]*\bhref="/[a-z][^"]*"[^>]*>(.*?)</a>', re.S | re.I)


def unwrap(html: str) -> str:
    return REL_LINK_RE.sub(r"\1", html)


def _dsn() -> str:
    load_dotenv(".env", encoding="utf-8-sig", override=False)
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    if "5.42.107.253" not in dsn:
        raise RuntimeError("DATABASE_URL не прод (5.42.107.253) — передай прод-DSN из .mcp.json")
    return dsn


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            rows = await conn.fetch(
                """SELECT id, content->>'text' AS t FROM materials
                    WHERE content->>'text' ~ 'href="/[a-z]' ORDER BY id""")
            print(f"Материалов с относительными ссылками: {len(rows)}")

            changed = 0
            for r in rows:
                new = unwrap(r["t"])
                if new == r["t"]:
                    continue
                # Инвариант: текст ссылки обязан сохраниться (разворачиваем, не удаляем).
                if len(new) >= len(r["t"]):
                    raise RuntimeError(f"{r['id']}: длина не уменьшилась — что-то не так")
                await conn.execute(
                    "UPDATE materials SET content = jsonb_set(content,'{text}', to_jsonb($2::text)) WHERE id=$1",
                    r["id"], new)
                changed += 1

            left = await conn.fetchval(
                "SELECT count(*) FROM materials WHERE content->>'text' ~ 'href=\"/[a-z]'")
            assert left == 0, f"остались относительные ссылки: {left}"
            print(f"Развёрнуто в текст: {changed} материалов; относительных ссылок осталось {left}")

            # Контроль: текст-подсказка выжил на примере 2355 (ОГЭ «Что нужно знать»).
            s = await conn.fetchval("SELECT content->>'text' FROM materials WHERE id=2355")
            if s:
                assert "Измерение информации" in s, "текст ссылки на 2355 потерян"
                print("Пример 2355: текст «Измерение информации…» сохранён, ссылки нет")

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
