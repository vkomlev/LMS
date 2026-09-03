# -*- coding: utf-8 -*-
"""tsk-779: вердикт чека медленных запросов считается по СВЕЖИМ суткам.

Зачем. Окно чека — неделя, и вердикт раньше считался по всему окну сразу.
Значит один разобранный и вылеченный затор поднимал тревогу ещё семь дней,
пока не выпадет из окна. Так и вышло: затор 29.08 починили в тот же день
(tsk-735), а понедельничная сводка звала на разбор до 03.09 — и он ушёл в
работу вторично, как живая проблема.

Проверяется чистая функция `verdict`: ей на вход только числа, база не нужна.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root / "scripts"))

from check_slow_requests import ALERT_BURST, ALERT_SECONDS, verdict  # noqa: E402


def test_свежий_долгий_запрос_это_пожар():
    """Один запрос дольше порога в свежих сутках — красный свет."""
    assert verdict(
        worst_sec=ALERT_SECONDS + 1, burst_n=1,
        fresh_worst_sec=ALERT_SECONDS + 1, fresh_hour_n=1,
    ) == "active"


def test_свежая_пачка_это_пожар():
    """Плотность за час в свежих сутках — тоже красный свет, даже если каждый
    запрос по отдельности порога не перешёл."""
    assert verdict(
        worst_sec=5.0, burst_n=ALERT_BURST,
        fresh_worst_sec=5.0, fresh_hour_n=ALERT_BURST,
    ) == "active"


def test_затор_в_окне_но_свежие_сутки_чистые_это_не_пожар():
    """Главный случай tsk-779: 29.08 было 20.8 с и пачка, с тех пор тихо.

    Раньше это семь дней подряд читалось как активный затор.
    """
    assert verdict(
        worst_sec=20.8, burst_n=ALERT_BURST + 10,
        fresh_worst_sec=13.9, fresh_hour_n=1,
    ) == "resolved"


def test_ничего_серьёзного_это_фон():
    """Единичный тяжёлый отчёт преподавателя будить оператора не должен."""
    assert verdict(
        worst_sec=6.6, burst_n=2, fresh_worst_sec=6.6, fresh_hour_n=2,
    ) == "background"


@pytest.mark.parametrize("fresh_worst", [ALERT_SECONDS - 0.1, ALERT_SECONDS])
def test_граница_порога_включительная(fresh_worst: float):
    """Ровно порог — уже пожар, чуть меньше — ещё нет.

    Граница проверяется отдельно: «>=» против «>» здесь меняет поведение чека
    на самых частых значениях, а заметно это станет только на живом заторе.
    """
    kind = verdict(
        worst_sec=fresh_worst, burst_n=1, fresh_worst_sec=fresh_worst, fresh_hour_n=1,
    )
    assert kind == ("active" if fresh_worst >= ALERT_SECONDS else "background")
