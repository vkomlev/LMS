"""tsk-581: страж синхронности режимов наставника между кодом и БД.

**Почему страж, а не просто фикс.** Живой дефект был не в логике, а в
рассинхроне: `prompt.pick_mode` научился возвращать `mission`, а
CHECK-ограничение `ck_ai_tutor_session_mode` о нём не знало — и открытие
наставника на задании-миссии падало 500 ученику. Ни один тест этого не поймал,
потому что все они проверяют выбор режима в памяти, а ограничение живёт в БД.
Следующий добавленный режим повторил бы то же самое буква в букву.

Тесты сверяют ДВЕ стороны рассинхрона:

1. Код знает больше БД — новый литерал `TutorMode` без миграции (исходный дефект,
   500 ученику на первой же сессии в этом режиме).
2. БД знает больше кода — значение разрешено ограничением, но код его не
   производит. Молчаливее первого: ничего не падает, просто в схеме живёт
   мёртвая метка, про которую через полгода никто не скажет, нужна она или это
   забытый хвост.

Оба класса ловятся сравнением множеств, а не перечислением значений: список
режимов в тесте пришлось бы править ровно так же забывчиво, как и миграцию.
"""
from __future__ import annotations

import re
from typing import get_args

import pytest
from sqlalchemy import text

from app.services.ai_tutor.prompt import TutorMode, TutorTaskView, _MODES, pick_mode

# Режимы, которые описаны и разрешены, но намеренно не выдаются `pick_mode`.
#
# `deepen` (углубление решения) сюда попал по факту: точки входа у него нет —
# наставник открывается только по заданию, и режим целиком выводится из
# формулировки и присланного ответа. Список не «разрешение забыть», а место,
# где мёртвая метка видна: если он растёт, режимы проектируются быстрее, чем
# подключаются.
RESERVED_MODES: frozenset[str] = frozenset({"deepen"})


def _modes_allowed_by_db_check(definition: str) -> set[str]:
    """Достать множество значений из текста CHECK-ограничения.

    PostgreSQL печатает `mode IN ('a','b')` как сравнение с массивом, где каждый
    элемент подписан типом. Забираем именно строковые литералы — так разбор не
    зависит от того, каким синтаксисом ограничение было создано.
    """
    return set(re.findall(r"'([a-z_]+)'", definition))


@pytest.mark.asyncio
async def test_db_check_allows_exactly_the_modes_code_knows(db):
    """Множество режимов в коде и в ограничении БД совпадает буква в букву."""
    definition = (
        await db.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'ck_ai_tutor_session_mode' "
                "  AND conrelid = 'ai_tutor_session'::regclass"
            )
        )
    ).scalar_one_or_none()
    assert definition, (
        "ограничение ck_ai_tutor_session_mode не найдено — либо миграции не "
        "накатаны на тестовую БД, либо ограничение переименовали, и тогда "
        "рассинхрон снова стал невидимым"
    )

    in_db = _modes_allowed_by_db_check(definition)
    in_code = set(get_args(TutorMode))

    assert in_code - in_db == set(), (
        f"код умеет режимы, которых нет в CHECK: {sorted(in_code - in_db)}. "
        "Ученик получит 500 при первой же сессии в таком режиме — нужна "
        "миграция, пересоздающая ck_ai_tutor_session_mode"
    )
    assert in_db - in_code == set(), (
        f"CHECK разрешает режимы, которых нет в TutorMode: {sorted(in_db - in_code)}. "
        "Мёртвое значение в схеме: либо подключить в коде, либо убрать миграцией"
    )


def test_every_declared_mode_has_a_prompt():
    """У каждого литерала `TutorMode` есть текст инструкции.

    Без этого `build_system_prompt` падал бы `KeyError` уже после успешной
    вставки в БД — то есть режим прошёл бы ограничение и умер ходом позже.
    """
    assert set(_MODES) == set(get_args(TutorMode))


def test_no_mode_is_declared_but_unreachable():
    """Каждый режим либо достижим из `pick_mode`, либо объявлен зарезервированным."""
    # Матрица покрывает все развилки `pick_mode`: миссия (маркеры приёмки),
    # тонкая задача (короткий стем / выбор варианта), код ученика, всё остальное.
    stems = {
        "mission": "МИССИЯ 1. Настрой среду и приложи скрин. Принято, если приложен файл.",
        "thin_short": "Что напечатает s[1:4]?",
        "long": "x" * 400 + "\nмного строк условия\nещё строка\n",
    }
    reachable: set[str] = set()
    for stem in stems.values():
        for single in (True, False):
            for has_code in (True, False):
                view = TutorTaskView(
                    task_id=1, stem=stem, task_type="SA_COM", is_single_construct=single
                )
                reachable.add(pick_mode(view, has_student_code=has_code))

    declared = set(get_args(TutorMode))
    unreachable = declared - reachable
    assert unreachable == set(RESERVED_MODES), (
        f"режимы объявлены, но не выдаются pick_mode: {sorted(unreachable)}. "
        "Либо подключить точку входа, либо внести в RESERVED_MODES с объяснением"
    )
