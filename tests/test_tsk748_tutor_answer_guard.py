"""tsk-748: страж не пускает готовое решение ученику.

Все образцы здесь — НАСТОЯЩИЕ реплики с боевой базы, а не выдуманные. Сессия 57
(ученик 142, задание 118, режим `concept`, 31.08): наставник спросил язык
программирования, сам предложил написать решение и написал его. Отвечала
`anthropic/claude-sonnet-4.6` — голова цепочки, при полностью доехавшей
инструкции. Проверять правку на придуманном тексте здесь бессмысленно: она
затем и пишется, что реальный ответ обошёл все текстовые запреты.

Обратная сторона так же важна: методика РАЗРЕШАЕТ микро-пример на посторонней
задаче (режим `concept`, шаг 3), и страж, режущий его, сломал бы объяснение.
Образец разрешённого — тоже живой, из сессии 5.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from app.services.ai_tutor import session_service
from app.services.ai_tutor.answer_guard import (
    BLOCKED_NOTICE,
    TutorStreamGuard,
    judge_block,
)

# Условие задания 118 — то, над которым сидел ученик.
STEM_118 = (
    "Напишите программу, которая считывает три целых числа (каждое с\n"
    "новой строки) и выводит их сумму. Программа должна выводить\n"
    "только число.\n\n"
    "Запустите программу с вводом:\n```\n847293781\n5194827365\n2938174625\n```\n"
    "Введите вывод программы в поле «Ответ»."
)

# Реплика 163: слив в чистом виде.
MSG_163 = """Вот готовая программа:

```python
a = int(input())
b = int(input())
c = int(input())
print(a + b + c)
```

**Как работает:**
1. `a = int(input())` — считывает первое число
2. `print(a + b + c)` — выводит их сумму

Программа готова к использованию!"""

# Реплика 159: «Пример программы» — тот же слив, поданный как справка по языку.
MSG_159 = """Отлично! В Python числа вводятся так:

**Ввод одного числа:**
```python
число = int(input())  # вводишь целое число, например: 42
```

**Пример программы:**
```python
# Вводим два числа и складываем их
a = int(input())  # первое число
b = int(input())  # второе число
сумма = a + b
print(сумма)
```

Какую задачу ты решаешь? Могу помочь с конкретным примером."""

# Реплика 44 сессии 5: разрешённый микро-пример на ПОСТОРОННЕЙ задаче.
MSG_44_ALLOWED = """Да, верно, 3**2=9.

Вот микро-пример на ** для вычисления объёма куба (сторона 4):

```
side = 4
volume = side ** 3
print(volume)
```

Что делает каждая строка?"""


def _run(text: str, *, mode: str = "concept", stem: str = STEM_118, step: int = 7):
    """Прогнать текст через стража кусками, как это делает поток."""
    guard = TutorStreamGuard(mode=mode, stem=stem)
    shown = ""
    for i in range(0, len(text), step):
        shown += guard.feed(text[i:i + step])
    shown += guard.finish()
    return guard, shown


def test_ready_solution_never_reaches_the_student():
    """Реплика 163: ученик не должен увидеть ни одной строки решения."""
    guard, shown = _run(MSG_163)
    assert guard.blocked
    assert "int(input())" not in shown
    assert "print(a + b + c)" not in shown
    assert BLOCKED_NOTICE.strip() in shown
    assert guard.hit is not None and "ввод" in guard.hit.reason


def test_solution_dressed_as_language_reference_is_cut_too():
    """Реплика 159: «Пример программы» — то же решение, поданное как справка.

    Первый блок (одна строка `int(input())`) законен и доезжает: это приём, а не
    решение. Обрыв происходит на «Примере программы» — втором блоке подряд, где
    собраны и ввод, и вывод.
    """
    guard, shown = _run(MSG_159)
    assert guard.blocked
    assert "print(сумма)" not in shown
    assert "a = int(input())  # первое число" not in shown


def test_micro_example_on_foreign_task_still_passes():
    """Разрешённое методикой не режем — иначе наставник перестанет объяснять."""
    guard, shown = _run(MSG_44_ALLOWED, stem="Вычисли 23 в степени 45")
    assert not guard.blocked
    assert "volume = side ** 3" in shown
    assert "Что делает каждая строка?" in shown


def test_thin_mode_forbids_any_assembled_code():
    """В `thin` даже посторонний пример равен ответу — ученик перенесёт числа."""
    guard, shown = _run(MSG_44_ALLOWED, mode="thin", stem="Что выведет s[1:4]?")
    assert guard.blocked
    assert "side = 4" not in shown


@pytest.mark.parametrize("step", [1, 3, 7, 40, 500])
def test_code_without_fence_is_caught(step: int):
    """Модель может забыть ограждение — фильтр по блокам такое пропустил бы.

    Размер куска здесь не косметика: поток приходит по несколько символов, и
    строка `a = int(input())` в руках фильтра оказывается кусками `a = int`,
    `(inpu`, `t())`. Первая версия отдавала такой хвост ученику сразу, потому что
    он «ещё не похож на код», — и голый код проезжал мимо детектора ВСЕГДА,
    оставаясь пойманным только в тесте с крупным куском.
    """
    guard, shown = _run(
        "Смотри:\na = int(input())\nb = int(input())\nprint(a + b)\nВот и всё.",
        step=step,
    )
    assert guard.blocked
    assert "b = int(input())" not in shown


def test_single_inline_code_line_survives():
    """Одна строка кода в объяснении законна: «введи `print(23**45)`»."""
    guard, shown = _run(
        "Открой IDLE.\nprint(23**45)\nЧто появилось в окне?\n", stem="степень"
    )
    assert not guard.blocked
    assert "print(23**45)" in shown
    assert "Что появилось в окне?" in shown


def test_example_built_on_task_numbers_is_blocked():
    """Пример «на других данных», куда подставлены числа из его же задания."""
    reason = judge_block(
        "x = 847293781\nprint(x)", mode="concept", stem=STEM_118, index=0
    )
    assert reason is not None and "данных задания" in reason


def test_long_listing_is_blocked():
    """Простыня в 10 строк — не иллюстрация приёма, а кусок программы."""
    code = "\n".join(f"шаг{i} = {i}" for i in range(10))
    assert judge_block(code, mode="concept", stem="", index=0) is not None


@pytest.mark.parametrize("step", [1, 3, 200])
def test_verdict_does_not_depend_on_how_the_network_splits_the_stream(step: int):
    """Сеть режет поток произвольно — ограждение не должно проскочить по частям."""
    guard, shown = _run(MSG_163, step=step)
    assert guard.blocked
    assert "int(input())" not in shown


def test_plain_reply_flows_and_does_not_wait_for_the_end():
    """Обычная реплика течёт ученику по мере генерации, а не копится до конца.

    Стриминг — весь смысл контура: без него ученик смотрит в пустой экран всё
    время ответа. Первая реплика наставника («Ошибки — это нормально…», 87
    символов) перевода строки не содержит вовсе, и придержать её до `finish`
    значило бы вернуть ровно ту задержку, ради устранения которой писался поток.
    """
    guard = TutorStreamGuard(mode="concept", stem=STEM_118)
    shown = guard.feed("Ошибки — это нормально, сейчас разберёмся: ")
    assert shown, "текст должен уйти ученику сразу, а не ждать конца ответа"
    shown += guard.feed("расскажи, как ты рассуждал?")
    shown += guard.finish()
    assert shown == "Ошибки — это нормально, сейчас разберёмся: расскажи, как ты рассуждал?"
    assert not guard.blocked


def test_allowed_answer_reaches_the_student_unchanged():
    """Разрешённый ответ доходит символ в символ: страж не переставляет строки."""
    guard, shown = _run(MSG_44_ALLOWED, stem="Вычисли 23 в степени 45")
    assert not guard.blocked
    assert shown.rstrip("\n") == MSG_44_ALLOWED.rstrip("\n")


# ───────────────── Кто отвечал: сводка модели на сессии ─────────────────────


async def _bare_session(db) -> tuple[int, int]:
    """Сессия без сообщений: здесь проверяется только запись в `meta`.

    Возвращает (id сессии, id созданного ученика) — оба удаляются в `finally`.
    """
    import random

    from app.models.users import Users

    task_id = (await db.execute(
        text("SELECT id FROM tasks ORDER BY id LIMIT 1")
    )).scalar()
    if task_id is None:
        pytest.skip("в базе нет ни одного задания")

    user = Users(
        email=f"tsk748-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None, full_name="страж", tg_id=None,
    )
    db.add(user)
    await db.flush()
    row = await db.execute(text("""
        INSERT INTO ai_tutor_session
            (student_id, task_id, mode, status, task_stem_snapshot)
        VALUES (:uid, :tid, 'concept', 'open', :stem) RETURNING id
    """), {"uid": user.id, "tid": task_id, "stem": STEM_118})
    sid = int(row.scalar_one())
    await db.commit()
    return sid, int(user.id)


async def _drop_session(db, sid: int, uid: int) -> None:
    await db.execute(text("DELETE FROM ai_tutor_session WHERE id = :sid"), {"sid": sid})
    await db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": uid})
    await db.commit()


async def _meta(db, sid: int) -> dict:
    row = await db.execute(
        text("SELECT meta FROM ai_tutor_session WHERE id = :sid"), {"sid": sid}
    )
    return row.scalar_one() or {}


@pytest.mark.asyncio
async def test_session_records_which_model_answered(db):
    """До 31.08 `meta` был пуст у всех 56 сессий — разбирать инцидент было нечем."""
    sid, uid = await _bare_session(db)
    try:
        await session_service.note_turn(db, sid, model="google/gemini-3.6-flash")
        await session_service.note_turn(db, sid, model="google/gemini-3.6-flash")
        await session_service.note_turn(db, sid, model="x-ai/grok-4.5")
        await db.commit()

        meta = await _meta(db, sid)
        assert meta["last_model"] == "x-ai/grok-4.5"
        assert meta["models"] == {"google/gemini-3.6-flash": 2, "x-ai/grok-4.5": 1}
        assert "guard_hits" not in meta
    finally:
        await _drop_session(db, sid, uid)


@pytest.mark.asyncio
async def test_guard_hit_is_recorded_next_to_the_model(db):
    """Слив без имени модели неразбираем: срабатывание и модель пишутся вместе."""
    sid, uid = await _bare_session(db)
    try:
        await session_service.note_turn(
            db, sid, model="anthropic/claude-sonnet-4.6",
            guard_hit={"reason": "законченная программа", "cut_chars": 75, "turn": 4},
        )
        await db.commit()

        meta = await _meta(db, sid)
        assert meta["last_model"] == "anthropic/claude-sonnet-4.6"
        assert len(meta["guard_hits"]) == 1
        assert meta["guard_hits"][0]["cut_chars"] == 75
    finally:
        await _drop_session(db, sid, uid)


@pytest.mark.asyncio
async def test_json_null_in_meta_does_not_break_the_record(db):
    """JSON-null в jsonb — не SQL NULL: `COALESCE` его пропускает, а `||` роняет."""
    sid, uid = await _bare_session(db)
    try:
        await db.execute(
            text("UPDATE ai_tutor_session SET meta = 'null'::jsonb WHERE id = :sid"),
            {"sid": sid},
        )
        await db.commit()
        await session_service.note_turn(db, sid, model="openai/gpt-5.5")
        await db.commit()

        assert (await _meta(db, sid))["last_model"] == "openai/gpt-5.5"
    finally:
        await _drop_session(db, sid, uid)
