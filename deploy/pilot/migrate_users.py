"""Перенести названных людей с боевого экземпляра на другой (tsk-789).

Зачем отдельный скрипт: «мигрировать пользователя» на этой платформе — не
копирование строки. У человека есть роли (отдельная таблица), а без зачисления
на корень курса он не увидит в кабинете ничего. Сделать это руками — значит
однажды забыть половину.

Что переносится: адрес почты, имя, роли, зачисление на указанные курсы.
Что НЕ переносится: учебная история, сессии, платежи, заявки. Это тестовые
учётки для прогона, а не перевоз ученика вместе с его работой.

Скрипт читает боевую базу ТОЛЬКО на чтение и пишет исключительно в целевую.
Отказывается работать, если целевая база совпадает с боевой.

    python migrate_users.py --emails a@b.ru,c@d.ru --target-env /opt/lms-pilot/.env \\
        --enroll HIM-DIPLOMAT            # сухой прогон
    python migrate_users.py … --apply    # запись
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

import asyncpg

PROD_ENV = "/opt/lms/.env"


def dsn(env_path: str) -> str:
    raw = re.search(
        r"^DATABASE_URL=(.+)$", Path(env_path).read_text(encoding="utf-8"), re.M
    ).group(1).strip()
    return raw.replace("postgresql+asyncpg://", "postgresql://")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--emails", required=True, help="через запятую")
    ap.add_argument("--source-env", default=PROD_ENV)
    ap.add_argument("--target-env", required=True)
    ap.add_argument("--enroll", default="", help="course_uid корней через запятую")
    ap.add_argument("--all-roles", action="store_true", help="выдать все роли справочника")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    emails = [e.strip() for e in args.emails.split(",") if e.strip()]
    src_dsn, dst_dsn = dsn(args.source_env), dsn(args.target_env)
    if src_dsn.rsplit("/", 1)[-1] == dst_dsn.rsplit("/", 1)[-1]:
        raise SystemExit("ОТКАЗ: источник и приёмник — одна и та же база")

    src = await asyncpg.connect(src_dsn, timeout=20)
    try:
        people = await src.fetch(
            "SELECT id, email, full_name FROM users WHERE email = ANY($1::text[])", emails
        )
    finally:
        await src.close()

    found = {p["email"] for p in people}
    for e in emails:
        if e not in found:
            print(f"ВНИМАНИЕ: в источнике нет {e}")
    if not people:
        raise SystemExit("никого не нашли — нечего переносить")

    dst = await asyncpg.connect(dst_dsn, timeout=20)
    try:
        db = await dst.fetchval("SELECT current_database()")
        print(f"приёмник: {db}")
        roles = await dst.fetch("SELECT id, name FROM roles ORDER BY id")
        print("роли в приёмнике:", ", ".join(r["name"] for r in roles))

        enroll_uids = [u.strip() for u in args.enroll.split(",") if u.strip()]
        courses = (
            await dst.fetch(
                "SELECT id, course_uid, title FROM courses WHERE course_uid = ANY($1::text[])",
                enroll_uids,
            )
            if enroll_uids
            else []
        )
        for uid in enroll_uids:
            if uid not in {c["course_uid"] for c in courses}:
                raise SystemExit(f"курс {uid} в приёмнике не найден")

        print()
        for p in people:
            existing = await dst.fetchval("SELECT id FROM users WHERE email = $1", p["email"])
            action = "обновить" if existing else "создать"
            print(f"  {action}: {p['full_name']} <{p['email']}>")
        print(f"  ролей каждому: {len(roles) if args.all_roles else 0}")
        print(f"  зачислить на: {', '.join(c['title'] for c in courses) or '—'}")

        if not args.apply:
            print("\nСухой прогон, ничего не записано. Повторить с --apply.")
            return 0

        for p in people:
            async with dst.transaction():
                uid = await dst.fetchval("SELECT id FROM users WHERE email = $1", p["email"])
                if uid is None:
                    uid = await dst.fetchval(
                        "INSERT INTO users (email, full_name, is_active) "
                        "VALUES ($1, $2, true) RETURNING id",
                        p["email"],
                        p["full_name"],
                    )
                    print(f"  создан {p['email']} -> id={uid}")
                else:
                    await dst.execute(
                        "UPDATE users SET full_name = $2, is_active = true WHERE id = $1",
                        uid,
                        p["full_name"],
                    )
                    print(f"  обновлён {p['email']} -> id={uid}")

                if args.all_roles:
                    for r in roles:
                        await dst.execute(
                            "INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2) "
                            "ON CONFLICT DO NOTHING",
                            uid,
                            r["id"],
                        )
                for c in courses:
                    await dst.execute(
                        "INSERT INTO user_courses (user_id, course_id) VALUES ($1, $2) "
                        "ON CONFLICT DO NOTHING",
                        uid,
                        c["id"],
                    )

        print("\n=== что получилось ===")
        rows = await dst.fetch(
            "SELECT u.id, u.email, u.full_name, u.is_active, "
            "  (SELECT count(*) FROM user_roles ur WHERE ur.user_id = u.id) AS roles, "
            "  (SELECT count(*) FROM user_courses uc WHERE uc.user_id = u.id) AS courses "
            "FROM users u ORDER BY u.id"
        )
        for r in rows:
            print(f"  id={r['id']} {r['full_name']} <{r['email']}> "
                  f"активен={r['is_active']} ролей={r['roles']} курсов={r['courses']}")
        return 0
    finally:
        await dst.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
