"""tsk-435: импорт реального расписания оператора (Яндекс.Календарь) в
Календарь LMS (групповые слоты, post-rework).

План построен вручную по данным приватного ICS-экспорта (не хранится и не
логируется — см. tasks/tsk-435 в Root-трекере) + сверке имён с реальными
LMS-аккаунтами (`SELECT id, full_name, ... FROM users`). Время в календаре —
Asia/Yekaterinburg (UTC+5); по решению оператора пересчитано в Europe/Moscow
(-2 часа, без смещения дня недели — проверено скриптом на этапе анализа).

Режимы:
- `--dry-run` (по умолчанию) — только печатает план, ничего не пишет.
- `--apply` — создаёт 11 недостающих аккаунтов (роль student, без email/tg_id
  — привязка через identity_link при первом реальном входе) и 12 групповых
  `lesson_slot` с участниками через `lesson_calendar_service` (тот же путь,
  что admin API — переиспользует валидацию ролей/пересечений).

5 «плавающих» учеников (Денис Ильин id=4501, Миша Поскребышев id=4509,
Рахимжанов Вадим id=4519, Курунов Кирилл Владимирович id=4522, Кирилл
Несскофи id=4523) НЕ участвуют ни в одном слоте — по решению оператора,
только ad-hoc запись в часы работы школы.

Запуск на проде: `DBCHECK_OK=1 venv/bin/python scripts/tsk435_import_calendar.py --apply`
(под пользователем app, см. docs/ai/operator-runbook.md R-009).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import time
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv(Path(__file__).resolve().parent.parent / ".env", encoding="utf-8-sig")

from app.db.session import async_session_factory  # noqa: E402
from app.services import lesson_calendar_service  # noqa: E402
from app.services.auth.role_assign_service import ensure_student_role  # noqa: E402
from app.models.users import Users  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tsk435_import")

TEACHER_ID = 2  # Виктор Комлев — организатор всех 35 событий календаря (проверено ORGANIZER)

# --- 11 недостающих учеников (нет совпадения с реальным LMS-аккаунтом) ---
# Ключ — символическая метка для участников ниже; full_name — как в календаре
# (аббревиатура фамилии сохранена как есть, без грейд-пометок вида "(8)").
NEW_STUDENTS: dict[str, str] = {
    "vladimir_gr": "Владимир Гр.",
    "danil_as": "Данил Ас.",
    "maksim_sun": "Максим Сун.",
    "ilya_chet": "Илья Чет.",
    "ilya_rv": "Илья Рв.",
    "andrey_zal": "Андрей Зал.",
    "kirill_kuz": "Кирилл Куз.",
    "elisey_ya": "Елисей Я.",
    "angelina_a": "Ангелина А.",
    "lazar": "Лазарь",
    "olga_om": "Ольга Ом.",
}

# --- 12 групповых слотов: (weekday MSK, start_time MSK, duration_minutes, участники) ---
# Участник — либо int (существующий user_id), либо строка-ключ из NEW_STUDENTS.
SLOTS: list[dict] = [
    {  # SA 11:00 MSK (YEKB 13:00)
        "weekday": 5, "start_time": time(11, 0), "duration_minutes": 60,
        "students": [4507, 4518, 4513, "vladimir_gr", 4524],
        "summary": "Егор Сел., Иван Моч., Илья Мих., Владимир Гр., Елена Я.",
    },
    {  # SA 10:00 MSK (YEKB 12:00)
        "weekday": 5, "start_time": time(10, 0), "duration_minutes": 60,
        "students": ["danil_as", "maksim_sun", 4520, 4512, 4504, "ilya_chet", 4517],
        "summary": "Данил Ас., Максим Сун., Денис Бел., Глеб Ан., Дмитрий Гал., Илья Чет., Артемий Н.",
    },
    {  # TU 10:00 MSK (YEKB 12:00)
        "weekday": 1, "start_time": time(10, 0), "duration_minutes": 60,
        "students": ["maksim_sun"],
        "summary": "Максим Сун.",
    },
    {  # MO 10:00 MSK (YEKB 12:00)
        "weekday": 0, "start_time": time(10, 0), "duration_minutes": 60,
        "students": ["danil_as", "ilya_rv", "andrey_zal", 4500, 4508],
        "summary": "Данил Ас., Илья Рв., Андрей Зал., Захар Г., Богдан Г.",
    },
    {  # WE 18:00 MSK (YEKB 20:00)
        "weekday": 2, "start_time": time(18, 0), "duration_minutes": 60,
        "students": [4518, 4497, "kirill_kuz"],
        "summary": "Иван М., Рита Х., Кирилл Куз.",
    },
    {  # WE 10:00 MSK (YEKB 12:00)
        "weekday": 2, "start_time": time(10, 0), "duration_minutes": 60,
        "students": [4511, "ilya_chet", 4504, "ilya_rv", "andrey_zal", 4508, 4505],
        "summary": "Владислав Лит., Илья Чет., Дмитрий Г., Илья Рв., Андрей Зал., Богдан Г., Джемаль.",
    },
    {  # TU 17:00 MSK (YEKB 19:00)
        "weekday": 1, "start_time": time(17, 0), "duration_minutes": 60,
        "students": [4517],
        "summary": "Артемий Н.",
    },
    {  # WE 11:00 MSK (YEKB 13:00)
        "weekday": 2, "start_time": time(11, 0), "duration_minutes": 60,
        "students": [
            "vladimir_gr", 4506, 4502, 4503, "lazar", 4521, 4507, 4513, 4524, 4515, 4516,
        ],
        "summary": (
            "Владимир Гр., Полина Гр., София Е., Анастасия К., Лазарь, Юлия, "
            "Егор С., Илья Мих., Елена Я, Андрей Л., Матвей К."
        ),
    },
    {  # WE 12:00 MSK (YEKB 14:00) — новый слот, старт 2026-07-29 в календаре
        "weekday": 2, "start_time": time(12, 0), "duration_minutes": 60,
        "students": [4498, 4499],
        "summary": "Михаил П., Достан М.",
    },
    {  # MO 11:00 MSK (YEKB 13:00)
        "weekday": 0, "start_time": time(11, 0), "duration_minutes": 60,
        "students": [
            "vladimir_gr", 4506, 4510, "elisey_ya", "angelina_a", "lazar", 4514, 4515, 4516,
        ],
        "summary": (
            "Владимир Гр., Полина Гр., Илья Тер., Елисей Я., Ангелина А., "
            "Лазарь, Эмиль Г., Андрей Л., Матвей К."
        ),
    },
    {  # TU 11:00 MSK (YEKB 13:00)
        "weekday": 1, "start_time": time(11, 0), "duration_minutes": 60,
        "students": [4502, 4503],
        "summary": "София Е., Анастасия К.",
    },
    {  # MO 17:00 MSK (YEKB 19:00)
        "weekday": 0, "start_time": time(17, 0), "duration_minutes": 60,
        "students": ["olga_om", 4497, "kirill_kuz"],
        "summary": "Ольга Ом., Рита Х., Кирилл Куз.",
    },
]

_WEEKDAY_NAMES = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


def _print_plan() -> None:
    print(f"Преподаватель: teacher_id={TEACHER_ID}\n")
    print(f"Новые аккаунты ({len(NEW_STUDENTS)}):")
    for key, name in NEW_STUDENTS.items():
        print(f"  [{key}] full_name={name!r}, role=student, email=None, tg_id=None")
    print(f"\nСлоты ({len(SLOTS)}):")
    for i, slot in enumerate(SLOTS, 1):
        weekday_name = _WEEKDAY_NAMES[slot["weekday"]]
        print(
            f"  #{i}: {weekday_name} {slot['start_time']} "
            f"({slot['duration_minutes']} мин) — {len(slot['students'])} участников"
        )
        print(f"      {slot['summary']}")


async def _apply() -> None:
    async with async_session_factory() as db:
        created_ids: dict[str, int] = {}
        for key, full_name in NEW_STUDENTS.items():
            user = Users(email=None, password_hash=None, full_name=full_name, tg_id=None)
            db.add(user)
            await db.flush()
            await ensure_student_role(
                db, user.id, channel="tsk435_import", origin="calendar_import",
            )
            created_ids[key] = user.id
            logger.info("Создан аккаунт [%s] id=%s full_name=%r", key, user.id, full_name)
        await db.commit()

        for i, slot in enumerate(SLOTS, 1):
            student_ids = [
                created_ids[s] if isinstance(s, str) else s for s in slot["students"]
            ]
            row = await lesson_calendar_service.create_lesson_slot(
                db,
                teacher_id=TEACHER_ID,
                weekday=slot["weekday"],
                start_time=slot["start_time"],
                duration_minutes=slot["duration_minutes"],
                timezone="Europe/Moscow",
                created_by=TEACHER_ID,
                student_ids=student_ids,
            )
            logger.info(
                "Слот #%s создан: id=%s weekday=%s start=%s участников=%s",
                i, row.id, slot["weekday"], slot["start_time"], len(student_ids),
            )

    print("\nГотово. Аккаунты и слоты созданы.")
    print(f"Созданные аккаунты: {created_ids}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Выполнить запись (по умолчанию — dry-run)")
    args = parser.parse_args()

    _print_plan()
    if not args.apply:
        print("\n[dry-run] Ничего не записано. Запустите с --apply для реальной записи.")
        return

    asyncio.run(_apply())


if __name__ == "__main__":
    main()
