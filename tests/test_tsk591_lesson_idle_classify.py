"""tsk-591: как тик решает, простой это или нет — без БД, чистой логикой.

Тесты этого модуля про ЛОЖНЫЕ ТРЕВОГИ. Сигнал, который врёт, преподаватель
перестанет читать через неделю, поэтому каждый случай «тревоги быть не должно»
здесь так же важен, как «тревога должна быть».
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.lesson_idle_cron_service import _Participant, classify, silent_since

_NOW = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)
_LESSON_START = _NOW - timedelta(minutes=30)
_THRESHOLD = timedelta(minutes=10)
_STALE = timedelta(seconds=420)


def _p(
    *,
    last_action_at=None,
    last_seen_at=None,
    last_interaction_at=None,
    context="task",
    lesson_start=_LESSON_START,
) -> _Participant:
    return _Participant(
        occurrence_id=1,
        student_id=42,
        student_name="Ученик",
        lesson_start=lesson_start,
        last_action_at=last_action_at,
        last_seen_at=last_seen_at,
        last_interaction_at=last_interaction_at,
        context=context,
        task_id=7,
        material_id=None,
        course_id=3,
        task_title="Задание",
        material_title=None,
    )


def _classify(p: _Participant):
    return classify(p, now=_NOW, threshold=_THRESHOLD, stale=_STALE)


# ── Тревоги быть не должно ───────────────────────────────────────────────────

def test_working_student_is_not_idle():
    """Сдал ответ минуту назад — работает."""
    p = _p(
        last_action_at=_NOW - timedelta(minutes=1),
        last_seen_at=_NOW - timedelta(seconds=30),
    )
    assert _classify(p) is None


def test_reading_and_scrolling_is_not_idle():
    """Читает материал и листает страницу.

    Содержательных действий нет уже 20 минут, но взаимодействие руками идёт —
    человек за экраном работает. Без этого различения тревога приходила бы на
    каждый длинный текст, и это первый способ научить преподавателя не читать
    сигнал.
    """
    p = _p(
        last_action_at=_NOW - timedelta(minutes=20),
        last_seen_at=_NOW - timedelta(seconds=30),
        last_interaction_at=_NOW - timedelta(minutes=2),
        context="material",
    )
    assert _classify(p) is None


def test_frontal_part_of_lesson_is_not_idle():
    """Начало урока: преподаватель объясняет, ученик ещё ничего не делал.

    Молчат все и это норма. Тик обязан молчать вместе с ними — иначе на каждом
    занятии он выдавал бы пачку тревог в первые же минуты.
    """
    p = _p(last_action_at=None, last_seen_at=_NOW - timedelta(seconds=30))
    assert _classify(p) is None


def test_student_never_in_lms_is_not_idle():
    """Урок идёт не в кабинете (разбор у доски, видеосвязь) — ученика нет вовсе.

    Ни пульса, ни действий. Тревоги нет: иначе на таком занятии сигнал
    сработал бы по всей группе и каждый раз был бы ложным.
    """
    p = _p(last_action_at=None, last_seen_at=None, last_interaction_at=None)
    assert _classify(p) is None


def test_action_before_lesson_start_does_not_arm_alarm():
    """Работал ДО начала занятия, а на самом занятии ещё нет.

    Тик получает действия только внутри окна занятия, поэтому такой участник
    приходит без `last_action_at` — тревоги быть не должно.
    """
    p = _p(last_action_at=None, last_seen_at=_NOW - timedelta(minutes=1))
    assert _classify(p) is None


# ── Тревога должна быть ──────────────────────────────────────────────────────

def test_worked_then_went_silent_is_idle():
    """Открыл задание, поработал и затих: кабинет открыт, действий нет.

    Ровно то, что просил оператор: «открыл задание и молчит».
    """
    p = _p(
        last_action_at=_NOW - timedelta(minutes=12),
        last_seen_at=_NOW - timedelta(seconds=30),
        last_interaction_at=_NOW - timedelta(minutes=12),
    )
    assert _classify(p) == "idle"
    assert silent_since(p, "idle") == _NOW - timedelta(minutes=12)


def test_worked_then_left_is_away():
    """Работал и пропал: пульса нет дольше порога — «вообще вне системы»."""
    p = _p(
        last_action_at=_NOW - timedelta(minutes=15),
        last_seen_at=_NOW - timedelta(minutes=14),
        last_interaction_at=_NOW - timedelta(minutes=15),
    )
    assert _classify(p) == "away"
    assert silent_since(p, "away") == _NOW - timedelta(minutes=14)


def test_single_missed_ping_is_not_away():
    """Один потерянный пульс — не повод объявлять ученика ушедшим.

    Пульс идёт раз в 2 минуты, свежим считается 7 минут. Между этими числами
    лежит запас на обрыв сети и на заснувший таймер вкладки.
    """
    p = _p(
        last_action_at=_NOW - timedelta(minutes=11),
        last_seen_at=_NOW - timedelta(minutes=4),
        last_interaction_at=_NOW - timedelta(minutes=11),
    )
    # Пульс ещё свежий → это «сидит и молчит», а не «ушёл».
    assert _classify(p) == "idle"


def test_worked_without_presence_client_still_detected():
    """Старый кабинет без пульса: работал и пропал — тревога всё равно нужна.

    Пульса нет вообще (`last_seen_at` пуст), но действия были. Тишина
    считается от последнего действия.
    """
    p = _p(last_action_at=_NOW - timedelta(minutes=13), last_seen_at=None)
    assert _classify(p) == "away"
    assert silent_since(p, "away") == _NOW - timedelta(minutes=13)


@pytest.mark.parametrize("minutes", [1, 5, 9])
def test_below_threshold_is_quiet(minutes: int):
    """Меньше порога — тишины ещё нет."""
    p = _p(
        last_action_at=_NOW - timedelta(minutes=minutes),
        last_seen_at=_NOW - timedelta(seconds=30),
    )
    assert _classify(p) is None


# ── Структурная защита (урок tsk-626 §10) ────────────────────────────────────

def test_worker_guard_is_transaction_scoped():
    """Сторож тика не должен брать СЕССИОННУЮ блокировку.

    В tsk-626 такая блокировка утекла на проде: она привязана к конкретному
    соединению, а сессия после коммита берёт из пула другое — ключ остался
    висеть, `pg_advisory_unlock` отработал вхолостую, и следующий тик молча
    решил бы, что работу делает другой worker. На dev это невидимо (пул пуст),
    поэтому поведенческим тестом такое не ловится — только текстом.
    """
    from pathlib import Path

    source = Path(
        lesson_idle_cron_service_file()
    ).read_text(encoding="utf-8")
    assert "pg_try_advisory_xact_lock" in source, "сторож должен быть транзакционным"
    assert "pg_try_advisory_lock(" not in source, "сессионная блокировка запрещена"
    assert "pg_advisory_unlock" not in source, "ручное снятие блокировки — признак сессионной"


def lesson_idle_cron_service_file() -> str:
    import app.services.lesson_idle_cron_service as module

    assert module.__file__ is not None
    return module.__file__
