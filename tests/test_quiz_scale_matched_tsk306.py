"""
Чистые юнит-тесты ``quiz_scale_matched`` (tsk-306), без БД.

Диагноз tsk-306: взрослый с интересом к Python выигрывал шкалу "питон"
argmax'ом наравне со школьником, а среди правил assignment_rule для этой
шкалы была только детская цель. Ключ ``none`` — исключающий список поверх
основного условия, чтобы одна и та же шкала-победитель могла вести в разные
правила в зависимости от того, есть ли параллельно сигнал другой аудитории.

Отдельный файл, а не test_assignment_rules_tsk031.py: та часть тестов —
async и работает с dev-БД (`pytestmark = pytest.mark.asyncio` на весь модуль),
эта — чистые синхронные функции без побочных эффектов.
"""
from __future__ import annotations

from app.services import assignment_rules_service as ars


def test_quiz_scale_argmax_unchanged_without_none():
    """Старое поведение (условие без ключа none) не меняется вообще."""
    totals = {"питон": 8, "школа": 2}
    assert ars.quiz_scale_matched({"mode": "argmax", "scale": "питон"}, totals) is True
    assert ars.quiz_scale_matched({"mode": "argmax", "scale": "школа"}, totals) is False


def test_quiz_scale_min_score_unchanged_without_none():
    """Старое поведение (min_score без none) не меняется вообще."""
    totals = {"работа": 3}
    assert ars.quiz_scale_matched({"min_score": 3, "scale": "работа"}, totals) is True
    assert ars.quiz_scale_matched({"min_score": 4, "scale": "работа"}, totals) is False


def test_quiz_scale_none_excludes_match():
    """tsk-306: питон argmax, но работа>=3 (взрослый сигнал) — правило не срабатывает."""
    totals = {"питон": 8, "работа": 6, "школа": 0}
    condition = {
        "mode": "argmax",
        "scale": "питон",
        "none": [{"scale": "работа", "min_score": 3}],
    }
    assert ars.quiz_scale_matched(condition, totals) is False


def test_quiz_scale_none_does_not_exclude_when_exclusion_false():
    """tsk-306: тот же школьный профиль (работа=0) — правило срабатывает как раньше."""
    totals = {"питон": 8, "работа": 0, "школа": 3}
    condition = {
        "mode": "argmax",
        "scale": "питон",
        "none": [{"scale": "работа", "min_score": 3}],
    }
    assert ars.quiz_scale_matched(condition, totals) is True


def test_quiz_scale_none_empty_list_is_noop():
    """Пустой список none ничего не исключает — эквивалент отсутствия ключа."""
    totals = {"питон": 8}
    condition = {"mode": "argmax", "scale": "питон", "none": []}
    assert ars.quiz_scale_matched(condition, totals) is True


def test_quiz_scale_missing_scale_never_matches():
    """Основного условия по отсутствующей в totals шкале не бывает — как и раньше."""
    assert ars.quiz_scale_matched({"mode": "argmax", "scale": "питон"}, {}) is False


def test_quiz_scale_tsk306_fallback_rule_for_adult_python_interest():
    """Новое правило-цель tsk-306: адресуется только simple min_score по "работа",
    полагаясь на то, что до него по порядку id уже отсеялись все argmax-правила
    (в т.ч. обновлённое питон-правило с исключением) — составное условие ему
    самому не нужно, только последовательность правил в БД."""
    adult_python_totals = {"питон": 8, "работа": 6, "бизнес": 3}
    fallback_condition = {"mode": "min_score", "scale": "работа", "min_score": 3}
    assert ars.quiz_scale_matched(fallback_condition, adult_python_totals) is True
