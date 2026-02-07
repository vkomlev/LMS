# Заполнение таблицы roles. Запуск из корня проекта: python scripts/seed_roles.py
# Соответствие: 1=student, 2=teacher, 3=methodist (см. app/repos/users_repo.py, docs).

import asyncio
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(dotenv_path=project_root / ".env", encoding="utf-8-sig")


async def main():
    from sqlalchemy import text
    from app.db.session import async_session_factory

    async with async_session_factory() as session:
        # Вставляем недостающие строки для пустой БД
        await session.execute(text("""
            INSERT INTO roles (id, name) VALUES
                (1, 'admin'), (2, 'methodist'), (3, 'teacher'), (4, 'student'),
                (5, 'marketer'), (6, 'customer')
            ON CONFLICT (id) DO NOTHING
        """))
        # Два шага: сначала временные имена (избегаем UniqueViolation при смене 1↔4, 2↔3), потом финальные
        await session.execute(text("""
            UPDATE roles SET name = '_seed_' || id WHERE id IN (1, 2, 3, 4, 5, 6)
        """))
        await session.execute(text("""
            UPDATE roles r SET name = v.name
            FROM (VALUES
                (1, 'admin'), (2, 'methodist'), (3, 'teacher'), (4, 'student'),
                (5, 'marketer'), (6, 'customer')
            ) AS v(id, name)
            WHERE r.id = v.id
        """))
        await session.commit()
        result = await session.execute(text("SELECT id, name FROM roles ORDER BY id"))
        rows = result.fetchall()
    check = {r[0]: r[1] for r in rows}
    print("OK: roles seeded (id=1 admin, id=2 methodist, id=3 teacher, id=4 student, id=5 marketer, id=6 customer).")
    print("Проверка в БД:", check)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception as e:
        print("FAIL:", e, file=sys.stderr)
        sys.exit(1)
