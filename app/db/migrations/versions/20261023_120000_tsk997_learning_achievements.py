"""tsk-997: каталог учебных достижений «за усилие».

**Данные, а не схема** — как у tsk-032: таблицы есть, миграция кладёт строки
каталога, правила проверки — в `app/services/achievements_engine.py`.

**Чего здесь нет намеренно.** Ни «с первой попытки», ни скорости, ни места
среди других: награждаем исправление ошибки, возврат к ней, рефлексию,
вопрос, закрытый модуль и пройденный срез (похвала за усилие, Дуэк).
`reward_points = 0`: валюты наград не заводим.

**«Закрыл модуль» — ступенями 1/3/10, а не повтором на каждый модуль.**
PK `user_achievements` — `(user_id, achievement_id)`: повторная выдача одной
строки невозможна (см. docstring миграции tsk-032), поэтому повторяемость
выражена ступенями.

**Откат** удаляет строки каталога, по `ON DELETE CASCADE` уходят и выданные
записи — правил в коде после отката тоже нет.

Revision ID: tsk997_learning_ach
Revises: tsk1403_materials_placement
Create Date: 2026-10-23
"""

from __future__ import annotations

import json
from typing import Sequence, Union

from alembic import op

revision: str = "tsk997_learning_ach"
down_revision: Union[str, None] = "tsk1403_materials_placement"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: (name, description, condition, reward_points)
_CATALOG: list[tuple[str, str, dict, int]] = [
    ("Довёл до верного",
     "Ошибся в задании и не бросил — разобрался и сдал верно.",
     {"type": "fixed_after_error", "count": 1}, 0),
    ("Довёл до верного: 5 заданий",
     "Пять заданий, где после ошибки ты дошёл до верного ответа.",
     {"type": "fixed_after_error", "count": 5}, 0),
    ("Довёл до верного: 20 заданий",
     "Двадцать заданий, доведённых до верного ответа после ошибки.",
     {"type": "fixed_after_error", "count": 20}, 0),
    ("Вернулся к ошибке",
     "Через несколько дней снова решил задание, в котором когда-то ошибся.",
     {"type": "review_passed", "count": 1}, 0),
    ("Вернулся к ошибкам: 5 заданий",
     "Пять заданий, к которым ты вернулся на повторении и решил снова.",
     {"type": "review_passed", "count": 5}, 0),
    ("Разобрался",
     "После разбора с преподавателем записал, что понял.",
     {"type": "reflection_done", "count": 1}, 0),
    ("Разобрался: 5 раз",
     "Пять разборов, после которых ты записал, что понял.",
     {"type": "reflection_done", "count": 5}, 0),
    ("Спросил, когда застрял",
     "Попросил помощи, когда задание не получалось. Это нормальный шаг в учёбе.",
     {"type": "help_asked", "count": 1}, 0),
    ("Закрыл модуль",
     "Сдал все обязательные задания модуля.",
     {"type": "module_completed", "count": 1}, 0),
    ("Закрыл три модуля",
     "Три модуля, в которых сданы все обязательные задания.",
     {"type": "module_completed", "count": 3}, 0),
    ("Закрыл десять модулей",
     "Десять модулей, в которых сданы все обязательные задания.",
     {"type": "module_completed", "count": 10}, 0),
    ("Прошёл итоговый срез",
     "Ответил на все задания итогового среза по курсу.",
     {"type": "final_check_done", "count": 1}, 0),
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
