# Мок-данные: 2–3 результата выполнения заданий на основе текущих users/tasks в БД.
# Запуск из корня проекта: python scripts/seed_task_results_mock.py

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
        # Проверяем, есть ли уже мок-результаты (чтобы не дублировать)
        r = await session.execute(
            text("SELECT COUNT(*) FROM task_results WHERE source_system = 'mock_seed'")
        )
        if (r.scalar() or 0) > 0:
            print("Мок task_results уже есть (source_system=mock_seed), пропуск.")
            return 0

        # Берём первого пользователя и несколько задач из БД
        users = (await session.execute(text("SELECT id FROM users LIMIT 1"))).fetchall()
        tasks = (
            await session.execute(
                text(
                    "SELECT id, max_score FROM tasks WHERE max_score IS NOT NULL ORDER BY id LIMIT 3"
                )
            )
        ).fetchall()

        if not users or not tasks:
            print("FAIL: Нет пользователей или задач с max_score в БД.")
            return 1

        user_id = users[0][0]
        # 2–3 записи: (task_id, score, max_score) — score чуть ниже max_score для реалистичности
        rows = []
        for i in range(min(3, len(tasks))):
            tid, mxs = tasks[i][0], tasks[i][1] or 10
            score = max(0, mxs - (i + 1)) if mxs else 5
            rows.append((tid, score, mxs))

        for task_id, score, max_score in rows:
            await session.execute(
                text("""
                    INSERT INTO task_results
                    (user_id, task_id, score, count_retry, max_score, is_correct, source_system)
                    VALUES
                    (:user_id, :task_id, :score, 0, :max_score, true, 'mock_seed')
                """),
                {
                    "user_id": user_id,
                    "task_id": task_id,
                    "score": score,
                    "max_score": max_score,
                },
            )
        await session.commit()

        # Проверка
        result = await session.execute(
            text(
                "SELECT id, user_id, task_id, score, max_score, source_system FROM task_results WHERE source_system = 'mock_seed' ORDER BY id"
            )
        )
        inserted = result.fetchall()
    print("OK: добавлено мок task_results:", len(inserted))
    for row in inserted:
        print("  ", dict(zip(["id", "user_id", "task_id", "score", "max_score", "source_system"], row)))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception as e:
        print("FAIL:", e, file=sys.stderr)
        sys.exit(1)
