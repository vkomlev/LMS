"""tsk-261 (класс «относительные ссылки»): убрать WP-блок навигации из LMS-материалов.

ДЕФЕКТ (приёмка QA 2026-07-16, находка B7): «ссылки битые». Проверено живьём: ссылка ведёт на
`learn.victor-komlev.ru/informatika-…` → 308 → **404**; тот же путь на `victor-komlev.ru` → 200.

ПОЧЕМУ НЕ АБСОЛЮТИЗИРУЕМ (решение оператора 2026-07-17 — правка курса важнее):
Первое побуждение — переписать href на `https://victor-komlev.ru/…`. Это неверно: мы бы выгоняли
ученика из LMS на WordPress. В LMS у навигации СВОЙ механизм (дерево курса, «← К курсу»,
next-item), а подкурс можно подключать к нескольким родителям — поэтому раздел подключается
подкурсом, а не ссылкой.

ЧТО ИМЕННО РЕЖЕМ: 500 из 565 относительных ссылок (в 131 материале) — это блок навигации
WP-проекции «Навигатор» (methodist/lms-wp-export требует его для L3-урока):
    <p><b>← <a …>Назад к уроку 1.5</a></b> · <a …>↑ В начало урока</a> ·
       <a …>⌂ В начало курса</a> · <b><a …>Вперёд →</a></b></p>
В LMS он и избыточен (навигация своя), и сломан (относительные пути → 404). Место этого блока —
только в WP-проекции.

ЧТО НЕ ТРОГАЕМ: 65 смысловых отсылок («Словарь бот-мейкера», «уроке 1.1», «системный блок») —
их адресат должен подключаться подкурсом (на ОГЭ Задании 1 узлы 832/1026 уже подключены, и
ссылки там просто дублируют граф). Это отдельная работа по графу, не по тексту.

БЕЗОПАСНОСТЬ ПРАВКИ: удаляем `<p>` ТОЛЬКО если его текст состоит ИСКЛЮЧИТЕЛЬНО из
навигационных сегментов (проверяется посегментно после снятия тегов). Абзац с содержательным
текстом не трогаем, даже если в нём есть nav-ссылка.

Запуск: dry-run по умолчанию; --apply (нужен DBCHECK_OK=1).
"""
import asyncio
import os
import re
import sys

import asyncpg
from dotenv import load_dotenv

# Навигационный сегмент: «Назад к уроку 1.5», «↑ В начало урока», «⌂ В начало курса», «Вперёд →».
NAV_SEG_RE = re.compile(
    r"^\s*[←→↑⌂\s]*(?:Назад|Вперёд|Вперед|Далее|В\s+начало|К\s+навигатору|К\s+уроку)\b[^·]*$",
    re.I,
)
P_RE = re.compile(r"<p\b[^>]*>(.*?)</p>", re.S | re.I)


def _text(html: str) -> str:
    t = re.sub(r"<[^>]+>", "", html)
    return t.replace("&nbsp;", " ").replace("\xa0", " ").strip()


def is_nav_only(inner_html: str) -> bool:
    """True, если абзац состоит ТОЛЬКО из навигационных сегментов."""
    txt = _text(inner_html)
    if not txt or "·" not in txt and not NAV_SEG_RE.match(txt):
        return False
    segs = [s for s in txt.split("·") if s.strip()]
    if not segs:
        return False
    return all(NAV_SEG_RE.match(s) for s in segs)


def strip_nav(html: str) -> tuple[str, int]:
    removed = 0

    def repl(m: re.Match) -> str:
        nonlocal removed
        if is_nav_only(m.group(1)):
            removed += 1
            return ""
        return m.group(0)

    out = P_RE.sub(repl, html)
    return re.sub(r"\n{3,}", "\n\n", out).strip(), removed


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
                """SELECT id, title, content->>'text' AS t FROM materials
                    WHERE content->>'text' ~ 'href="/[a-z]' ORDER BY id"""
            )
            print(f"Материалов с относительными ссылками: {len(rows)}")

            changed = 0
            removed_total = 0
            emptied = []
            for r in rows:
                new, removed = strip_nav(r["t"])
                if not removed:
                    continue
                # Инвариант: материал не должен опустеть — режем навигацию, не содержание.
                if len(_text(new)) < 40:
                    emptied.append(r["id"])
                    continue
                await conn.execute(
                    "UPDATE materials SET content = jsonb_set(content,'{text}', to_jsonb($2::text)) WHERE id=$1",
                    r["id"], new,
                )
                changed += 1
                removed_total += removed

            assert not emptied, f"правка опустошила бы материалы: {emptied[:5]}"
            print(f"Изменено материалов: {changed}; удалено nav-абзацев: {removed_total}")

            # Верификация в транзакции.
            nav_left = await conn.fetchval(
                """SELECT count(*) FROM materials
                    WHERE content->>'text' ~ 'В начало курса|Назад к уроку|Вперёд →'"""
            )
            rel_left = await conn.fetchval(
                """SELECT count(*) FROM materials WHERE content->>'text' ~ 'href="/[a-z]'"""
            )
            print(f"Проверка: материалов с nav-текстом осталось {nav_left}; "
                  f"с относительными ссылками {rel_left} (это смысловые отсылки — по плану остаются)")

            sample = await conn.fetchrow("SELECT content->>'text' s FROM materials WHERE id=3288")
            if sample:
                print(f"Пример 3288, хвост: {sample['s'][-90:]!r}")

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
