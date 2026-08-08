"""tsk-032: наполнение каталога достижений под удержание между занятиями.

**Данные, а не схема.** Таблицы `achievements`/`user_achievements` существуют
давно и всё это время были ПУСТЫ (0 строк на проде, проверено 2026-08-08) —
каркас заложили и ни разу не использовали. Миграция ничего не создаёт, она
кладёт в каркас каталог вех. Логика проверки условий — в коде
(`app/services/retention_service.py`), здесь только данные.

**Почему пороги такие.** Замер по проду до внедрения
(`reviews/2026-08-08-tsk032-retention-baseline.md`): за 12 недель порога
«7 дней подряд» не достиг ни один ученик из 49 — исходный летний стрик
выкинут. Недельные пороги 1/3/6/12 ложатся на факт: 2 недели подряд держали
19 учеников, 3 — восемь, рекорд 5. Объёмные пороги 10/25/50/100/250 — при
медиане 13 элементов за всё время; первые два порога закроют уже сделанным
(это осознанное признание прошлой работы, а не аванс), дальше идёт запас
роста.

**Про `is_recurring`.** Флаг в схеме есть, но PK `user_achievements` —
`(user_id, achievement_id)`, то есть физически повторная выдача невозможна.
Поэтому весь каталог — однократные вехи (`is_recurring = false`), а текущая
серия НЕ достижение: она производная и считается на лету (см. docstring
сервиса). Заводить «повторяемое» достижение в такой схеме значило бы обещать
поведение, которого нет.

**Про откат.** `downgrade` удаляет строки каталога; по FK
`ON DELETE CASCADE` вместе с ними уйдут и записи `user_achievements` —
то есть уже полученные учениками вехи. Это осознанно: после отката правил
проверки в коде тоже нет, а висящие ссылки на несуществующий каталог хуже.

Revision ID: tsk032_retention_ach
Revises: tsk581_tutor_mission
Create Date: 2026-08-08
"""

from __future__ import annotations

import json
from typing import Sequence, Union

from alembic import op

revision: str = "tsk032_retention_ach"
down_revision: Union[str, None] = "tsk581_tutor_mission"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: (name, description, condition, reward_points)
_CATALOG: list[tuple[str, str, dict, int]] = [
    (
        "Неделя между занятиями",
        "Позанимался между уроками хотя бы в один день за неделю.",
        {"type": "weekly_streak", "weeks": 1},
        10,
    ),
    (
        "Три недели подряд",
        "Три недели подряд возвращался к занятиям в промежутках между уроками.",
        {"type": "weekly_streak", "weeks": 3},
        30,
    ),
    (
        "Шесть недель подряд",
        "Полтора месяца без единой пропущенной недели.",
        {"type": "weekly_streak", "weeks": 6},
        60,
    ),
    (
        "Двенадцать недель подряд",
        "Три месяца подряд — привычка заниматься между уроками закрепилась.",
        {"type": "weekly_streak", "weeks": 12},
        120,
    ),
    (
        "10 шагов между занятиями",
        "Десять заданий и материалов, закрытых между уроками с преподавателем.",
        {"type": "between_lessons_items", "count": 10},
        10,
    ),
    (
        "25 шагов между занятиями",
        "Двадцать пять заданий и материалов, закрытых вне уроков.",
        {"type": "between_lessons_items", "count": 25},
        25,
    ),
    (
        "50 шагов между занятиями",
        "Полсотни заданий и материалов, закрытых вне уроков.",
        {"type": "between_lessons_items", "count": 50},
        50,
    ),
    (
        "100 шагов между занятиями",
        "Сто заданий и материалов, закрытых вне уроков.",
        {"type": "between_lessons_items", "count": 100},
        100,
    ),
    (
        "250 шагов между занятиями",
        "Двести пятьдесят заданий и материалов, закрытых вне уроков.",
        {"type": "between_lessons_items", "count": 250},
        250,
    ),
]


def upgrade() -> None:
    # ON CONFLICT (name) — миграция идемпотентна: повторный прогон на базе,
    # где каталог уже есть (напр. после ручного наполнения на dev), не падает
    # и не двоит строки.
    for name, description, condition, points in _CATALOG:
        op.execute(
            "INSERT INTO achievements (name, description, condition, reward_points, is_recurring) "
            "VALUES ("
            f"  {_lit(name)}, {_lit(description)}, "
            f"  CAST({_lit(json.dumps(condition, ensure_ascii=False))} AS jsonb), "
            f"  {int(points)}, false"
            ") ON CONFLICT (name) DO NOTHING"
        )


def downgrade() -> None:
    names = ", ".join(_lit(name) for name, _d, _c, _p in _CATALOG)
    op.execute(f"DELETE FROM achievements WHERE name IN ({names})")


def _lit(value: str) -> str:
    """SQL-литерал строки с экранированием одинарных кавычек."""
    escaped = value.replace("'", "''")
    return f"'{escaped}'"
