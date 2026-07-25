"""Точечная правка задания 9069 (курс 1047): текст вариантов "А"/"Б" переименован
в "Алгоритм А"/"Алгоритм Б", чтобы не конфликтовать с позиционной подписью
варианта "А./Б./В." (tsk-216, SPW commit 2369c98). correct_options и id вариантов
не меняются — правится только отображаемый текст.

Запуск на прод-сервере (см. docs/ai/operator-runbook.md R-009):
ssh lms-spw-vds 'sudo -u app bash -lc "cd /opt/lms && DBCHECK_OK=1 venv/bin/python scripts/fix_task9069_letter_option_text_tsk216.py --apply"'
Без --apply — dry-run (только чтение и сравнение, без записи).
"""

import argparse
import asyncio
import logging

from sqlalchemy import select

from app.db.session import async_session_factory
from app.models.tasks import Tasks

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

TASK_ID = 9069
RENAME_MAP = {"a": "Алгоритм А", "b": "Алгоритм Б"}


async def main(apply: bool) -> None:
    async with async_session_factory() as session:
        result = await session.execute(select(Tasks).where(Tasks.id == TASK_ID))
        task = result.scalar_one()

        options = task.task_content.get("options") or []
        logger.info("До правки: %s", [(o["id"], o["text"]) for o in options])

        correct_options = (task.solution_rules or {}).get("correct_options")
        if correct_options != ["a"]:
            raise RuntimeError(
                f"Инвариант нарушен: correct_options ожидался ['a'], получен {correct_options}. Останов."
            )

        changed = False
        new_options = []
        for opt in options:
            opt = dict(opt)
            if opt["id"] in RENAME_MAP and opt["text"] == {"a": "А", "b": "Б"}[opt["id"]]:
                opt["text"] = RENAME_MAP[opt["id"]]
                changed = True
            new_options.append(opt)

        if not changed:
            logger.info("Текст вариантов уже не голая буква (изменения не требуются) — останов.")
            return

        logger.info("После правки (план): %s", [(o["id"], o["text"]) for o in new_options])

        if not apply:
            logger.info("Dry-run: запись НЕ выполнена. Повторите с --apply для применения.")
            return

        new_content = dict(task.task_content)
        new_content["options"] = new_options
        task.task_content = new_content
        await session.commit()
        logger.info("COMMIT — изменения сохранены для задания %s.", TASK_ID)

        result = await session.execute(select(Tasks).where(Tasks.id == TASK_ID))
        verify_task = result.scalar_one()
        verify_options = verify_task.task_content.get("options") or []
        logger.info("Верификация после commit: %s", [(o["id"], o["text"]) for o in verify_options])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Реально записать изменения (иначе dry-run)")
    args = parser.parse_args()
    asyncio.run(main(apply=args.apply))
