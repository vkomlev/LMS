# tests/test_text_authorship_tsk646.py
"""
tsk-646: признак ИИ-авторства для развёрнутых текстовых работ.

Что закрываем:
- механические следы вставки находятся там, где они есть, и НЕ находятся на
  живой ученической прозе (обе группы примеров — с прода, дословно);
- короткий ответ на разбор не идёт вовсе: на трёх строках признака нет ни у кого;
- фоновый тик разбирает текстовую работу отдельной рубрикой и без линтера;
- при недоступной модели остаются следы вставки — они считаются без сети, и
  именно они проверяемы глазами;
- значок в списке зажигается и от следов, а не только от вердикта модели;
- отчёт по-прежнему не виден ученику.

Вызовы модели замоканы. Живой замер на боевом корпусе — отдельно,
docs/qa/2026-08-23-tsk646-text-authorship-calibration.md.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from typing import Any, Dict

import pytest
from sqlalchemy import text

from app.schemas.code_review import build_code_review_badge
from app.services import code_review_cron_service
from app.services.text_authorship_service import (
    MIN_TEXT_CHARS,
    detect_paste_signals,
    pick_text_for_review,
)


# ─────────────────────────── Ось следов вставки ──────────────────────────────
#
# Обе группы примеров взяты с прода дословно (2026-08-23). Это принципиально:
# признак, обвиняющий ребёнка, нельзя калибровать на выдуманных примерах —
# выдуманный «ученический стиль» всегда получается карикатурным, и порог
# уезжает в сторону обвинения.

#: Работа 17177 ученицы 4538 — та самая, про которую преподаватель сказал
#: «просто нейросеть». Формула разорвана по строкам и повторена собранной:
#: так текст выглядит, когда его копируют из окна чата, где формула нарисована.
_PROD_AI_PASTE = (
    "подставляем в формулу периметра: \n"
    "p\n=\n2\n⋅\n(\na\n+\nb\n)\np=2⋅(a+b);\n"
    "вычисляем сумму в скобках: \n6\n+\n4\n=\n10\n6+4=10;\n"
    "умножаем на 2: \n2\n⋅\n10\n=\n20\n2⋅10=20.\n"
    "Итоговый результат: Периметр: 20"
)

#: Живые ученические ответы (4536, 4552, 4533, 4554) — те, что писали руками.
_PROD_STUDENT_PROSE = [
    "число: 10101₂\n\n1) перевод в десятичную:\nвеса: 16, 8, 4, 2, 1\n"
    "1*16 + 0*8 + 1*4 + 0*2 + 1*1 = 16+0+4+0+1 = 21\n\n"
    "2) обратный перевод (21 в двоичную):\n21 / 2 = 10 (ост. 1)\n10 / 2 = 5 (ост. 0)\n"
    "5 / 2 = 2 (ост. 1)\n2 / 2 = 1 (ост. 0)\n1 / 2 = 0 (ост. 1)\nчитаем снизу вверх: 10101",
    "1) N = 2 × 2 × 2 × 2 × 2 = 32\n2)\n┌─┬─┬─┬─┬\n│1│0│1│1│0│\n└─┴─┴─┴─┴\n"
    "3) 10110₂ = 1×16 + 0×8 + 1×4 + 1×2 + 0×1 = 16 + 4 + 2 = 22₁₀\n"
    "Диапазон: от 0 до 31: 22 входит.",
    "составное высказывание: \"сегодня на улице идёт дождь ИЛИ я поел суп на обед\".\n\n"
    "простые высказывания: A = \"сегодня на улице идёт дождь\", B = \"я поел суп на обед\". "
    "связка - \nИЛИ.\n\nдля сегодняшнего дня моё высказывание истинно, потому что дождя "
    "на улице нет (A - ложно), но я на обед действительно ел суп (B - истинно).",
    "55 кб • 1024 байт=56320 байт\n56320 • 8 бит=450 560 бит \nN=2i\nN=64 \ni= 6\nОтвет: 6 бит",
    "А B  не B  А и (не B)\n0  0      1         0\n0  1      0         0\n"
    "1  0      1         1\n1  1      0         0",
]


def test_math_render_residue_is_found_in_real_pasted_work() -> None:
    """След вставки формул из окна чата находится и несёт кусок текста."""
    signals = detect_paste_signals(_PROD_AI_PASTE)
    codes = [s["code"] for s in signals]
    assert "math_render_residue" in codes
    found = next(s for s in signals if s["code"] == "math_render_residue")
    # Кусок текста обязателен: без него признак снова становится «похоже на ИИ»
    # без оснований — то есть ровно тем, с чего задача началась.
    assert found["evidence"]
    assert found["label"]


def test_handwritten_student_prose_has_no_paste_signals() -> None:
    """
    Живая ученическая проза следов не даёт — ни одна из пяти работ.

    Это главная проверка модуля. Ложное обвинение дороже пропуска: сработай
    ось здесь, и преподаватель получил бы повод для разговора с ребёнком,
    который ничего не списывал. Примеры намеренно «неудобные»: там и таблицы
    псевдографикой, и знаки «×», «•», «₂», и арифметика в столбик.
    """
    for prose in _PROD_STUDENT_PROSE:
        assert detect_paste_signals(prose) == [], prose[:60]


def test_single_stray_operator_line_is_not_enough() -> None:
    """
    Один знак на своей строке — не след. Лишний Enter вообразим, два подряд нет.

    Порог в два знака стоит именно здесь: он отделяет опечатку от разорванной
    по строкам формулы.
    """
    assert detect_paste_signals("сумма равна\n=\n42, дальше считаем по формуле") == []


def test_latex_and_markdown_residue_are_found() -> None:
    """Разметка, которую поле ответа не делает и не понимает."""
    latex = detect_paste_signals("Периметр равен \\(2 \\cdot (a+b)\\), подставим числа.")
    assert [s["code"] for s in latex] == ["latex_markup"]

    md = detect_paste_signals("**Вывод:** алгоритм завершается, потому что счётчик убывает.")
    assert [s["code"] for s in md] == ["markdown_residue"]


def test_signals_never_raise_on_garbage() -> None:
    """Функция зовётся из приёма ответа ученика — падать ей нельзя."""
    for junk in (None, "", 42, {"a": 1}, []):
        assert detect_paste_signals(junk) == []  # type: ignore[arg-type]


# ────────────────────────────── Порог длины ──────────────────────────────────


def test_short_answer_is_not_reviewed() -> None:
    """
    «Твой любимый цвет — синий» (реальная работа с прода) на разбор не идёт.

    Короткий ответ пишется одинаково и школьником, и моделью. Вердикт по нему
    был бы монеткой, а монетка, поданная преподавателю как признак, хуже
    отсутствия признака: ей поверят.
    """
    assert pick_text_for_review("Твой любимый цвет — синий") is None
    assert pick_text_for_review(None) is None
    assert pick_text_for_review("   ") is None


def test_long_answer_is_reviewed_and_trimmed() -> None:
    long_text = "а" * MIN_TEXT_CHARS
    assert pick_text_for_review(f"  {long_text}  ") == long_text


# ──────────────────────────── Фоновый тик ────────────────────────────────────


async def _seed_pending_text(db, body: str) -> int:
    """Развёрнутая работа, помеченная к разбору. Текст лежит в `response.text`."""
    course_id = (await db.execute(text(
        "INSERT INTO courses (title, access_level) VALUES ('tsk646', 'manual_check') RETURNING id"
    ))).scalar_one()
    task_id = (await db.execute(text(
        "INSERT INTO tasks (external_uid, max_score, task_content, solution_rules, course_id, difficulty_id) "
        "VALUES (:ext, 6, CAST(:c AS jsonb), CAST(:r AS jsonb), :cid, 1) RETURNING id"
    ), {
        "ext": f"tsk646-{random.randint(10**8, 10**10)}",
        "c": json.dumps({"type": "TA", "stem": "Опиши свой циклический алгоритм."}),
        "r": json.dumps({"max_score": 6}),
        "cid": course_id,
    })).scalar_one()
    user_id = (await db.execute(text("SELECT id FROM users ORDER BY id LIMIT 1"))).scalar_one()
    now = datetime.now(timezone.utc)
    result_id = (await db.execute(text(
        "INSERT INTO task_results (score, user_id, task_id, submitted_at, count_retry, received_at, "
        " max_score, source_system, answer_json, code_review) "
        "VALUES (0, :u, :t, :now, 0, :now, 6, 'test', CAST(:a AS jsonb), CAST(:cr AS jsonb)) RETURNING id"
    ), {
        "u": user_id, "t": task_id, "now": now,
        "a": json.dumps({"type": "TA", "response": {"text": body}}),
        "cr": json.dumps({"status": "pending", "kind": "text", "code": body}),
    })).scalar_one()
    await db.commit()
    return result_id


async def _cleanup(db, result_id: int) -> None:
    await db.execute(text(
        "DELETE FROM courses WHERE id IN "
        "(SELECT course_id FROM tasks WHERE id IN (SELECT task_id FROM task_results WHERE id = :r))"
    ), {"r": result_id})
    await db.commit()


async def _read_review(db, result_id: int) -> Dict[str, Any]:
    return (await db.execute(
        text("SELECT code_review FROM task_results WHERE id = :r"), {"r": result_id},
    )).scalar_one()


async def test_tick_reviews_text_work_without_linter(
    db, db_session_factory, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Текстовая работа разбирается своей рубрикой; линтер к прозе не зовётся."""
    body = _PROD_AI_PASTE + " " + "и так далее по шагам. " * 10
    result_id = await _seed_pending_text(db, body)

    async def _fake_text_review(text_, *, task_stem=None, student_id=None):
        return {
            "kind": "text",
            "ai_authorship": {"verdict": "ai_likely", "reasoning": "служебная структура"},
            "signals": [{"code": "math_render_residue", "label": "…", "evidence": "…"}],
            "model": "test-model",
        }

    def _linter_must_not_run(code):  # pragma: no cover — срабатывание = провал теста
        raise AssertionError("линтер не должен вызываться на прозе")

    monkeypatch.setattr(
        code_review_cron_service.text_authorship, "review_student_text", _fake_text_review
    )
    monkeypatch.setattr(
        code_review_cron_service, "analyze_student_code_quality", _linter_must_not_run
    )

    try:
        summary = await code_review_cron_service.code_review_cron_tick(db_session_factory)
        assert summary["reviewed"] >= 1

        review = await _read_review(db, result_id)
        assert review["status"] == "done"
        assert review["kind"] == "text"
        assert review["ai_authorship"]["verdict"] == "ai_likely"
        assert review["signals"][0]["code"] == "math_render_residue"
        # Чистоты кода у прозы нет и быть не должно.
        assert "code_quality" not in review
        assert "static" not in review
    finally:
        await _cleanup(db, result_id)


async def test_tick_skips_short_text(
    db, db_session_factory, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Короткая работа снимается с очереди с честной причиной, а не крутится вечно."""
    result_id = await _seed_pending_text(db, "коротко")
    # Снимок в очереди тоже короткий — но живой путь его туда и не положил бы.
    await db.execute(text(
        "UPDATE task_results SET code_review = CAST(:cr AS jsonb) WHERE id = :r"
    ), {"cr": json.dumps({"status": "pending", "kind": "text"}), "r": result_id})
    await db.commit()

    async def _never_called(*a, **kw):  # pragma: no cover
        raise AssertionError("модель не должна вызываться для короткой работы")

    monkeypatch.setattr(
        code_review_cron_service.text_authorship, "review_student_text", _never_called
    )

    try:
        await code_review_cron_service.code_review_cron_tick(db_session_factory)
        review = await _read_review(db, result_id)
        assert review["status"] == "skipped"
        assert review["reason"] == "too_short"
    finally:
        await _cleanup(db, result_id)


async def test_paste_signals_survive_model_failure(
    db, db_session_factory, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Модель не ответила — следы вставки всё равно доезжают до преподавателя.

    Ровно тот же принцип, что у кода с линтером (находка ревью tsk-302 Б1):
    при недоступной модели фича обязана деградировать до проверяемой части,
    а не исчезать. Здесь проверяемая часть даже ценнее вердикта: её видно глазами.
    """
    body = _PROD_AI_PASTE + " " + "и так далее по шагам. " * 10
    result_id = await _seed_pending_text(db, body)

    async def _model_down(text_, *, task_stem=None, student_id=None):
        return {
            "kind": "text",
            "signals": [{"code": "math_render_residue", "label": "…", "evidence": "…"}],
            "error": "LLMConfigError",
            "message": "нет ключа",
            "retryable": False,
        }

    monkeypatch.setattr(
        code_review_cron_service.text_authorship, "review_student_text", _model_down
    )

    try:
        summary = await code_review_cron_service.code_review_cron_tick(db_session_factory)
        assert summary["degraded"] >= 1

        review = await _read_review(db, result_id)
        assert review["status"] == "done"
        assert review["degraded"] is True
        assert review["signals"][0]["code"] == "math_render_residue"
    finally:
        await _cleanup(db, result_id)


async def test_no_signals_and_no_model_is_an_honest_failure(
    db, db_session_factory, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Нечего показать — так и пишем `failed`, а не «признаков не нашли»."""
    result_id = await _seed_pending_text(db, "а" * (MIN_TEXT_CHARS + 10))

    async def _model_down(text_, *, task_stem=None, student_id=None):
        return {"kind": "text", "signals": [], "error": "LLMConfigError", "retryable": False}

    monkeypatch.setattr(
        code_review_cron_service.text_authorship, "review_student_text", _model_down
    )

    try:
        summary = await code_review_cron_service.code_review_cron_tick(db_session_factory)
        assert summary["failed"] >= 1
        review = await _read_review(db, result_id)
        assert review["status"] == "failed"
    finally:
        await _cleanup(db, result_id)


async def test_text_work_stays_pending_on_temporary_failure(
    db, db_session_factory, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Временный сбой оставляет работу в очереди вместе со снимком текста."""
    body = "а" * (MIN_TEXT_CHARS + 10)
    result_id = await _seed_pending_text(db, body)

    async def _timeout(text_, *, task_stem=None, student_id=None):
        return {"kind": "text", "signals": [], "error": "LLMTimeout", "retryable": True}

    monkeypatch.setattr(
        code_review_cron_service.text_authorship, "review_student_text", _timeout
    )

    try:
        summary = await code_review_cron_service.code_review_cron_tick(db_session_factory)
        assert summary["retried"] >= 1
        review = await _read_review(db, result_id)
        assert review["status"] == "pending"
        assert review["kind"] == "text"
        assert review["code"] == body
        assert review["attempts"] == 1
    finally:
        await _cleanup(db, result_id)


# ──────────────────────────────── Значок ─────────────────────────────────────


def test_badge_lights_up_from_paste_signals_alone() -> None:
    """
    Следы есть, вердикта модели нет — значок обязан гореть.

    Иначе работа, где нашлась проверяемая часть признака, выглядела бы в ленте
    чистой именно потому, что не сработала НЕпроверяемая часть.
    """
    badge = build_code_review_badge({
        "status": "done",
        "kind": "text",
        "signals": [{"code": "math_render_residue"}],
    })
    assert badge is not None
    assert badge.ai_suspected is True


def test_badge_stays_dark_without_any_signal() -> None:
    badge = build_code_review_badge({
        "status": "done",
        "kind": "text",
        "signals": [],
        "ai_authorship": {"verdict": "ambiguous"},
    })
    assert badge is not None
    assert badge.ai_suspected is False
