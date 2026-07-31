"""tsk-478: живая проверка на проде — bootstrap тестовой сессии родителя без
email round-trip (auth/test/issue-session заблокирован в prod намеренно).

Создаёт тестового пользователя БЕЗ ролей, привязывает его как родителя к
реальному ученику через ParentStudentLinksService (тот же код, что и прод
API — auto-assign роли `parent`), минтит сессионный токен через
session_service.create_session (тот же внутренний сервис, что использует
production auth flow — не публичный bypass-эндпоинт).

Запуск: на сервере под app (sudo -u app), из /opt/lms:
  venv/bin/python scripts/tsk478_live_parent_check.py --student-id 4508 setup
  venv/bin/python scripts/tsk478_live_parent_check.py --student-id 4508 cleanup
"""
from __future__ import annotations

import argparse
import asyncio
import os
import random
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(dotenv_path=os.path.join(PROJECT_ROOT, ".env"), encoding="utf-8-sig")

from app.api.main import app as _app  # noqa: E402,F401 — форсирует правильный порядок импорта моделей
from app.db.session import get_async_db  # noqa: E402
from app.models.users import Users  # noqa: E402
from app.services.auth import identity_link_service  # noqa: E402
from app.services.auth.session_service import create_session  # noqa: E402
from app.services.parent_student_links_service import ParentStudentLinksService  # noqa: E402
from sqlalchemy import text  # noqa: E402

_TAG = "tsk478-live-check"


async def setup(student_id: int) -> None:
    async for db in get_async_db():
        u = Users(
            email=f"{_TAG}-{random.randint(10**8, 10**10)}@example.invalid",
            password_hash=None,
            full_name=f"{_TAG}-parent",
            tg_id=None,
        )
        db.add(u)
        await db.flush()
        await identity_link_service.upsert_identity(db, u.id, "email", u.email)
        await db.commit()

        service = ParentStudentLinksService()
        await service.add_link(db, u.id, student_id)

        token, _, _ = await create_session(db, user_id=u.id)
        await db.commit()

        print(f"parent_user_id={u.id}")
        print(f"session_token={token}")
        return


async def cleanup(student_id: int) -> None:
    async for db in get_async_db():
        rows = (
            await db.execute(
                text("SELECT id FROM users WHERE email LIKE :pat"),
                {"pat": f"{_TAG}-%@example.invalid"},
            )
        ).fetchall()
        ids = [r[0] for r in rows]
        if not ids:
            print("nothing to clean up")
            return
        await db.execute(text("DELETE FROM users WHERE id = ANY(:ids)"), {"ids": ids})
        await db.commit()
        print(f"deleted test users: {ids}")
        return


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["setup", "cleanup"])
    parser.add_argument("--student-id", type=int, required=True)
    args = parser.parse_args()

    if args.action == "setup":
        await setup(args.student_id)
    else:
        await cleanup(args.student_id)


if __name__ == "__main__":
    asyncio.run(main())
