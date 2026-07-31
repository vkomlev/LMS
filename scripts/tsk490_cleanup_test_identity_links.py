"""tsk-490: убрать тестовые email-привязки с настоящих учётных записей.

`identity_link` с `kind='email'` — это ДЕЙСТВУЮЩИЙ способ войти. Тесты
`test_identity_linking.py` до этой задачи брали первого попавшегося
пользователя (`SELECT MIN(id) FROM users`), вешали на него привязку и
коммитили без уборки. Запущенные однажды по боевой базе, они оставили на
аккаунте оператора (админ + методист + преподаватель) три постоянных ключа
входа на несуществующем домене.

Источник починен в самом тесте (своя одноразовая учётка); здесь убирается то,
что успело накопиться.

Защита: строка удаляется, только если у пользователя после удаления остаётся
хотя бы одна привязка. Остаться без единого способа войти нельзя.

Запуск (прод — на сервере, под app):
    python scripts/tsk490_cleanup_test_identity_links.py            # разбор
    DBCHECK_OK=1 python scripts/tsk490_cleanup_test_identity_links.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import logging

from sqlalchemy import text

from app.db.session import async_session_factory

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("tsk490")

# Шаблоны, которые оставляли именно эти тесты. Никаких «всё, что example.com»:
# на example.com бывают и легитимные заведённые вручную учётки.
_PATTERNS = (
    "test.upsert.%@example.com",
    "upper.case.test.%@example.com",
    "idempotent.%@example.com",
)

_SELECT = text(
    """
    SELECT il.id, il.user_id, il.value, il.created_at, il.last_used_at,
           u.full_name,
           (SELECT count(*) FROM identity_link o WHERE o.user_id = il.user_id) AS links_total
    FROM identity_link il
    JOIN users u ON u.id = il.user_id
    WHERE il.kind = 'email'
      AND (il.value LIKE :p0 OR il.value LIKE :p1 OR il.value LIKE :p2)
    ORDER BY il.user_id, il.id
    """
)

# След использования: выдавалась ли по этому адресу ссылка для входа и вошли ли
# по ней. Удалять то, чем реально пользовались, нельзя без разбора.
_USAGE = text(
    """
    SELECT count(*) AS issued, count(consumed_at) AS consumed
    FROM magic_link WHERE lower(email) = lower(:value)
    """
)

_DELETE = text("DELETE FROM identity_link WHERE id = ANY(:ids)")


async def main(apply: bool) -> int:
    params = {f"p{i}": p for i, p in enumerate(_PATTERNS)}
    async with async_session_factory() as db:
        rows = (await db.execute(_SELECT, params)).all()
        if not rows:
            logger.info("Тестовых привязок нет — чистить нечего.")
            return 0

        logger.info("Найдено привязок: %d", len(rows))
        safe: list[int] = []
        for r in rows:
            usage = (await db.execute(_USAGE, {"value": r.value})).one()
            keeps = r.links_total - 1
            if usage.consumed:
                mark = "ПРОПУСК (по адресу входили)"
            elif keeps < 1:
                mark = "ПРОПУСК (осталась бы учётка без входа)"
            else:
                mark = "УДАЛИТЬ"
                safe.append(r.id)
            logger.info(
                "  [%s] id=%s · %s (%s, id=%s) · заведена %s · ссылок выдано %s, "
                "входов %s · останется привязок: %s",
                mark, r.id, r.value, r.full_name, r.user_id,
                r.created_at, usage.issued, usage.consumed, keeps,
            )

        if not apply:
            logger.info("\nРазбор. Для записи: DBCHECK_OK=1 ... --apply")
            return 0
        if not safe:
            logger.info("Удалять нечего.")
            return 0

        await db.execute(_DELETE, {"ids": safe})
        left = [r.id for r in (await db.execute(_SELECT, params)).all() if r.id in safe]
        if left:
            await db.rollback()
            logger.error("Строки остались после удаления: %s — откат.", left)
            return 1
        await db.commit()
        logger.info("Удалено привязок: %d.", len(safe))
        return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="выполнить удаление")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.apply)))
