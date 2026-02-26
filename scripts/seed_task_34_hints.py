"""
Добавить текстовую и видеоподсказку к заданию с ID=34 для тестирования hints на фронте.

Подсказки:
- hints_text: URL статьи про списки/генераторы в Python
- hints_video: URL видео VK

Запуск из корня проекта:
  python scripts/seed_task_34_hints.py

Идемпотентно: повторный запуск перезаписывает подсказки у задачи 34.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(dotenv_path=project_root / ".env", encoding="utf-8-sig")

TASK_ID = 34
HINTS_TEXT = [
    "https://victor-komlev.ru/spiski-massivy-v-python/#generatory-spiskov",
]
HINTS_VIDEO = [
    "https://vk.com/video-53400615_456239810",
]


async def main() -> int:
    from sqlalchemy import select, update, text
    from app.db.session import async_session_factory
    from app.models.tasks import Tasks

    async with async_session_factory() as session:
        r = await session.execute(select(Tasks).where(Tasks.id == TASK_ID))
        task = r.scalar_one_or_none()
        if not task:
            print(f"Задание с id={TASK_ID} не найдено.")
            return 1

        tc = dict(task.task_content) if task.task_content else {}
        tc["hints_text"] = HINTS_TEXT
        tc["hints_video"] = HINTS_VIDEO

        await session.execute(
            update(Tasks).where(Tasks.id == TASK_ID).values(task_content=tc)
        )
        await session.commit()
        print(f"Задание id={TASK_ID}: добавлены hints_text (1 URL) и hints_video (1 URL).")
        print("  hints_text:", HINTS_TEXT[0])
        print("  hints_video:", HINTS_VIDEO[0])
    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
