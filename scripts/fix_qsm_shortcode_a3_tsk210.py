"""Точечная чистка мёртвых QSM-шорткодов в 3 материалах (tsk-210, находка A3).

Курс 106 «Первая программа на Python» — материалы 237/238/239 содержат
литеральный `[qsm quiz=NN]` (26/27/25) в `content->>'text'`. Плагин QSM был
заброшен (переход на LMS), шорткоды — мёртвый текст, видимый ученику на странице.

Удаляем ровно токен вместе с ведущим переводом строки: regexp
`\\r\\n\\[qsm quiz=\\d+\\]` → '' в `content.text`. Остальная разметка не трогается.

Безопасность (/db-check Режим записи):
- по умолчанию DRY-RUN (только пред-выборка и план, без записи);
- `--apply` пишет В ОДНОЙ ТРАНЗАКЦИИ: pre-count → UPDATE → post-count/verify →
  commit только если в 3 материалах не осталось `[qsm`, иначе rollback;
- затрагивает ТОЛЬКО id 237/238/239.

Рецидив: если материал позже переиздать из ContentBackbone (где исходник ещё с
шорткодом) — вернётся. QSM заброшен, переиздание не планируется; при переиздании
источник должен снимать `[qsm ...]` (корень — в CB-публикаторе).

Запуск (из корня LMS, прод-DATABASE_URL в окружении):
  python scripts/fix_qsm_shortcode_a3_tsk210.py            # dry-run
  python scripts/fix_qsm_shortcode_a3_tsk210.py --apply    # запись
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv

load_dotenv(dotenv_path=project_root / ".env", encoding="utf-8-sig")

MATERIAL_IDS = [237, 238, 239]
# \r\n перед токеном + сам [qsm quiz=NN]
QSM_RE = r"\r\n\[qsm quiz=\d+\]"


async def main() -> int:
    parser = argparse.ArgumentParser(description="Чистка QSM-шорткодов A3 (tsk-210)")
    parser.add_argument("--apply", action="store_true", help="Записать (по умолчанию dry-run)")
    args = parser.parse_args()

    from sqlalchemy import text
    from app.db.session import async_session_factory

    async with async_session_factory() as s:
        print("=== A3 QSM-shortcode cleanup (tsk-210) ===")
        print(f"Режим: {'APPLY' if args.apply else 'DRY-RUN'}  | материалы: {MATERIAL_IDS}")

        # pre: показать план — длина text и наличие шорткода
        rows = (await s.execute(
            text("""
                SELECT id,
                       length(content->>'text') AS len,
                       (content->>'text') ~ :re AS has_qsm
                FROM materials WHERE id = ANY(:ids) ORDER BY id
            """),
            {"ids": MATERIAL_IDS, "re": QSM_RE},
        )).fetchall()
        for r in rows:
            print(f"  material {r[0]}: len={r[1]} has_qsm={r[2]}")

        if not args.apply:
            print("\nDRY-RUN: изменения НЕ записаны. Для записи добавь --apply.")
            return 0

        # apply в транзакции: UPDATE → verify → commit/rollback
        await s.execute(
            text("""
                UPDATE materials
                SET content = jsonb_set(
                    content, '{text}',
                    to_jsonb(regexp_replace(content->>'text', :re, '', 'g')),
                    false
                )
                WHERE id = ANY(:ids)
            """),
            {"ids": MATERIAL_IDS, "re": QSM_RE},
        )

        # verify (в той же транзакции, до commit)
        after = (await s.execute(
            text("""
                SELECT id,
                       length(content->>'text') AS len,
                       ((content->>'text') ILIKE '%[qsm%') AS still_has
                FROM materials WHERE id = ANY(:ids) ORDER BY id
            """),
            {"ids": MATERIAL_IDS},
        )).fetchall()
        print("\nПосле UPDATE (до commit):")
        remaining = 0
        for r in after:
            print(f"  material {r[0]}: len={r[1]} still_has_qsm={r[2]}")
            if r[2]:
                remaining += 1

        if remaining == 0:
            await s.commit()
            print(f"\nCOMMIT: {len(MATERIAL_IDS)} материалов очищены, [qsm не осталось.")
            return 0
        else:
            await s.rollback()
            print(f"\nROLLBACK: в {remaining} материалах остался [qsm — записи НЕ было.")
            return 1


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception as e:  # noqa: BLE001
        print("FAIL:", e, file=sys.stderr)
        sys.exit(1)
