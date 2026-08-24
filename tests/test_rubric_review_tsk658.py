# tests/test_rubric_review_tsk658.py
"""
tsk-658: покритериальный разбор развёрнутых ответов по рубрике задания.

Что закрываем:
- пункты и их веса собираются из `text_answer.rubric` через единую точку
  `criteria_for_judge()`; задание без критериев разбора не получает;
- балл складывает НАШ код, а не модель: `unclear` баллов не даёт, а у критериев
  без весов предложенного балла нет вовсе;
- пункт, о котором модель промолчала, остаётся в отчёте как `unclear` — рубрика
  не может стать короче настоящей;
- фоновый тик кладёт раскладку рядом с признаком авторства, отдельным вызовом,
  и делает это даже когда вердикт о слоге не получен;
- временный сбой авторства оставляет работу в очереди и НЕ платит за разбор
  рубрики заранее;
- рубрика ученику не видна — она живёт в `code_review`, как и весь отчёт.

Вызовы модели замоканы: здесь проверяется механика сборки и записи, а не
качество суждений модели. Калибровка на живых сдачах — отдельно.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from typing import Any, Dict

import pytest
from sqlalchemy import text

from app.services import code_review_cron_service, rubric_review_service


_RUBRIC = {
    "max_score": 6,
    "text_answer": {
        "auto_check": False,
        "rubric": [
            {"id": "c1", "title": "Выписаны 4–5 команд прибора", "max_score": 1},
            {"id": "c2", "title": "Названы 2 команды, которых у прибора нет", "max_score": 2},
            {"id": "c3", "title": "Есть вывод о формальности исполнителя", "max_score": 3},
        ],
    },
    "manual_review_required": True,
}

_ANSWER = (
    "Я взял микроволновку. Она понимает команды: разогреть, разморозить, поставить "
    "время, стоп, открыть дверцу. А вот команд «приготовь вкусно» и «позвони маме» "
    "у неё нет. Она формальный исполнитель, потому что делает ровно то, что нажали, "
    "и всегда одинаково."
)


# ───────────────────────── Сборка пунктов рубрики ────────────────────────────


def test_rubric_items_are_taken_with_weights() -> None:
    items = rubric_review_service.rubric_items(_RUBRIC)
    assert [item["id"] for item in items] == ["c1", "c2", "c3"]
    assert [item["max_score"] for item in items] == [1, 2, 3]


def test_task_without_criteria_is_not_reviewed() -> None:
    assert rubric_review_service.rubric_items({"max_score": 6}) == []


def test_broken_rules_do_not_raise() -> None:
    """Правка `solution_rules` мимо API — рабочий случай, а не исключение."""
    assert rubric_review_service.rubric_items("не словарь") == []
    assert rubric_review_service.rubric_items({"text_answer": {"rubric": "мусор"}}) == []


def test_criteria_without_weights_give_items_but_no_score() -> None:
    """Критерии из `grading_criteria` весов не имеют — цифру предлагать нечем."""
    rules = {
        "max_score": 4,
        "grading_criteria": {
            "must": ["Назван признак", "Приведён свой пример"],
            "accept": [],
            "reject": [],
            # `approved` без `reviewed_by` схема не принимает: подтверждение без
            # имени подтвердившего не отличить от заготовки (tsk-590).
            "status": "approved",
            "reviewed_by": 2,
        },
    }
    items = rubric_review_service.rubric_items(rules)
    assert len(items) == 2
    assert all(item["max_score"] is None for item in items)


# ──────────────────────────── Счёт баллов ────────────────────────────────────


async def test_score_is_summed_by_us_and_unclear_gives_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Балл складывает код. `unclear` — повод посмотреть, а не половина зачёта."""
    async def _fake_complete(messages, **kwargs):
        payload = json.dumps({
            "items": [
                {"id": "c1", "met": "yes", "evidence": "разогреть, разморозить"},
                {"id": "c2", "met": "unclear", "evidence": "названа одна"},
                {"id": "c3", "met": "yes", "evidence": "делает ровно то, что нажали"},
            ],
            "summary": "Команды есть, вывод есть.",
        })
        return type("R", (), {"text": payload, "model": "test-model"})()

    monkeypatch.setattr(rubric_review_service, "complete", _fake_complete)

    result = await rubric_review_service.review_against_rubric(
        _ANSWER, solution_rules=_RUBRIC, task_stem="Опиши прибор как исполнителя.",
    )
    review = result["rubric_review"]
    assert review["suggested_score"] == 4  # 1 + 3; unclear (2 балла) не даёт
    assert review["max_score"] == 6
    assert [item["met"] for item in review["items"]] == ["yes", "unclear", "yes"]


async def test_missing_item_stays_unclear(monkeypatch: pytest.MonkeyPatch) -> None:
    """Пункт, о котором модель промолчала, не исчезает из рубрики."""
    async def _fake_complete(messages, **kwargs):
        payload = json.dumps({"items": [{"id": "c1", "met": "yes", "evidence": "…"}]})
        return type("R", (), {"text": payload, "model": "test-model"})()

    monkeypatch.setattr(rubric_review_service, "complete", _fake_complete)

    review = (await rubric_review_service.review_against_rubric(
        _ANSWER, solution_rules=_RUBRIC,
    ))["rubric_review"]
    assert len(review["items"]) == 3
    assert [item["met"] for item in review["items"]] == ["yes", "unclear", "unclear"]
    assert review["suggested_score"] == 1


async def test_model_verdict_of_full_score_is_not_taken_on_trust(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Модель не назначает балл: даже присланное ею число игнорируется."""
    async def _fake_complete(messages, **kwargs):
        payload = json.dumps({
            "suggested_score": 6,
            "items": [{"id": "c2", "met": "no", "evidence": "нет второй команды"}],
        })
        return type("R", (), {"text": payload, "model": "test-model"})()

    monkeypatch.setattr(rubric_review_service, "complete", _fake_complete)

    review = (await rubric_review_service.review_against_rubric(
        _ANSWER, solution_rules=_RUBRIC,
    ))["rubric_review"]
    assert review["suggested_score"] == 0


@pytest.mark.parametrize("broken", ['[{"id": "c1"}]', '"просто строка"', "не json вовсе"])
async def test_broken_model_answer_does_not_crash_the_tick(
    broken: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Кривой ответ модели становится записью об ошибке, а не исключением.

    Важно именно про массив: `data.get` на списке бросает `AttributeError`, и
    он не входит в перехват — один такой ответ уронил бы фоновый тик вместе с
    работами, которые в пачке ещё не разобраны.
    """
    async def _fake_complete(messages, **kwargs):
        return type("R", (), {"text": broken, "model": "test-model"})()

    monkeypatch.setattr(rubric_review_service, "complete", _fake_complete)

    review = (await rubric_review_service.review_against_rubric(
        _ANSWER, solution_rules=_RUBRIC,
    ))["rubric_review"]
    assert review["error"] == "unparsable_verdict"
    assert review["retryable"] is True


async def test_short_answer_is_not_sent_to_model(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _never_called(*a, **kw):  # pragma: no cover — вызов = провал теста
        raise AssertionError("короткий ответ на разбор не идёт")

    monkeypatch.setattr(rubric_review_service, "complete", _never_called)
    assert await rubric_review_service.review_against_rubric(
        "коротко", solution_rules=_RUBRIC,
    ) == {}


# ──────────────────────────── Фоновый тик ────────────────────────────────────


async def _seed_pending_text(db, body: str, rules: Dict[str, Any]) -> int:
    """Развёрнутая работа с рубрикой, помеченная к разбору."""
    course_id = (await db.execute(text(
        "INSERT INTO courses (title, access_level) VALUES ('tsk658', 'manual_check') RETURNING id"
    ))).scalar_one()
    task_id = (await db.execute(text(
        "INSERT INTO tasks (external_uid, max_score, task_content, solution_rules, course_id, difficulty_id) "
        "VALUES (:ext, 6, CAST(:c AS jsonb), CAST(:r AS jsonb), :cid, 1) RETURNING id"
    ), {
        "ext": f"tsk658-{random.randint(10**8, 10**10)}",
        "c": json.dumps({"type": "TA", "stem": "Опиши прибор как исполнителя."}),
        "r": json.dumps(rules),
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


async def test_tick_writes_rubric_next_to_authorship(
    db, db_session_factory, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Раскладка по рубрике ложится в отчёт рядом с признаком авторства."""
    body = _ANSWER + " Ещё раз: она делает только то, что нажали. " * 3
    result_id = await _seed_pending_text(db, body, _RUBRIC)

    async def _fake_text_review(text_, *, task_stem=None, student_id=None):
        return {
            "kind": "text",
            "ai_authorship": {"verdict": "student_likely", "reasoning": "свой пример"},
            "signals": [],
            "model": "test-model",
        }

    async def _fake_rubric(text_, *, solution_rules, task_stem=None, student_id=None):
        return {"rubric_review": {
            "items": [{"id": "c1", "title": "…", "max_score": 1, "met": "yes", "evidence": "…"}],
            "suggested_score": 1, "max_score": 6, "summary": "часть есть",
        }}

    monkeypatch.setattr(
        code_review_cron_service.text_authorship, "review_student_text", _fake_text_review
    )
    monkeypatch.setattr(
        code_review_cron_service.rubric_review_service, "review_against_rubric", _fake_rubric
    )

    try:
        summary = await code_review_cron_service.code_review_cron_tick(db_session_factory)
        assert summary["reviewed"] >= 1

        review = await _read_review(db, result_id)
        assert review["status"] == "done"
        assert review["ai_authorship"]["verdict"] == "student_likely"
        assert review["rubric_review"]["suggested_score"] == 1
        # Зачёт остаётся за человеком: тик вердикта работе не ставит.
        is_correct = (await db.execute(
            text("SELECT is_correct FROM task_results WHERE id = :r"), {"r": result_id},
        )).scalar_one()
        assert is_correct is None
    finally:
        await _cleanup(db, result_id)


async def test_rules_reach_the_review_from_the_database(
    db, db_session_factory, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Критерии доезжают из БД до разбора — без моков между ними.

    Здесь замокан только вызов модели: остальное идёт живым путём — выборка
    очереди, распаковка строки, сборка пунктов. Это ловит класс ошибок, который
    моки прячут целиком: перепутанный порядок колонок при распаковке (их в
    строке четырнадцать) выглядел бы как «рубрики просто нет».
    """
    body = _ANSWER + " Добавлю ещё пару предложений, чтобы ответ был длинным. " * 2
    result_id = await _seed_pending_text(db, body, _RUBRIC)

    async def _authorship_silent(text_, *, task_stem=None, student_id=None):
        return {"kind": "text", "signals": [], "model": "test-model"}

    async def _fake_complete(messages, **kwargs):
        # Заодно убеждаемся, что до модели доехали и условие, и сами критерии.
        user_message = messages[-1].content
        assert "Названы 2 команды" in user_message
        assert "Опиши прибор как исполнителя" in user_message
        payload = json.dumps({
            "items": [
                {"id": "c1", "met": "yes", "evidence": "разогреть, разморозить"},
                {"id": "c2", "met": "yes", "evidence": "приготовь вкусно, позвони маме"},
                {"id": "c3", "met": "no", "evidence": "вывода нет"},
            ],
            "summary": "Команды названы, вывода не хватает.",
        })
        return type("R", (), {"text": payload, "model": "test-model"})()

    monkeypatch.setattr(
        code_review_cron_service.text_authorship, "review_student_text", _authorship_silent
    )
    monkeypatch.setattr(rubric_review_service, "complete", _fake_complete)

    try:
        await code_review_cron_service.code_review_cron_tick(db_session_factory)
        review = await _read_review(db, result_id)
        assert review["status"] == "done"
        rubric = review["rubric_review"]
        assert [item["title"] for item in rubric["items"]] == [
            "Выписаны 4–5 команд прибора",
            "Названы 2 команды, которых у прибора нет",
            "Есть вывод о формальности исполнителя",
        ]
        assert rubric["suggested_score"] == 3  # 1 + 2; третий пункт не засчитан
        assert rubric["max_score"] == 6
    finally:
        await _cleanup(db, result_id)


async def test_rubric_survives_authorship_failure(
    db, db_session_factory, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Разбор по критериям доезжает до преподавателя и без вердикта о слоге."""
    body = _ANSWER + " Повторю мысль другими словами ещё раз. " * 3
    result_id = await _seed_pending_text(db, body, _RUBRIC)

    async def _authorship_down(text_, *, task_stem=None, student_id=None):
        return {
            "kind": "text", "signals": [],
            "error": "LLMConfigError", "message": "ключ не задан", "retryable": False,
        }

    async def _fake_rubric(text_, *, solution_rules, task_stem=None, student_id=None):
        return {"rubric_review": {
            "items": [{"id": "c3", "title": "…", "max_score": 3, "met": "yes", "evidence": "…"}],
            "suggested_score": 3, "max_score": 6, "summary": "вывод есть",
        }}

    monkeypatch.setattr(
        code_review_cron_service.text_authorship, "review_student_text", _authorship_down
    )
    monkeypatch.setattr(
        code_review_cron_service.rubric_review_service, "review_against_rubric", _fake_rubric
    )

    try:
        await code_review_cron_service.code_review_cron_tick(db_session_factory)
        review = await _read_review(db, result_id)
        # Работа закрыта, отчёт неполный, но полезная часть в нём есть.
        assert review["status"] == "done"
        assert review["degraded"] is True
        assert review["rubric_review"]["suggested_score"] == 3
    finally:
        await _cleanup(db, result_id)


async def test_temporary_failure_does_not_pay_for_rubric(
    db, db_session_factory, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Работа остаётся в очереди целиком — за разбор дважды не платим."""
    body = _ANSWER + " И ещё немного текста для длины ответа ученика. " * 3
    result_id = await _seed_pending_text(db, body, _RUBRIC)

    async def _timeout(text_, *, task_stem=None, student_id=None):
        return {"kind": "text", "signals": [], "error": "LLMTimeout", "retryable": True}

    async def _must_not_run(*a, **kw):  # pragma: no cover — вызов = провал теста
        raise AssertionError("разбор рубрики не должен идти перед повтором")

    monkeypatch.setattr(
        code_review_cron_service.text_authorship, "review_student_text", _timeout
    )
    monkeypatch.setattr(
        code_review_cron_service.rubric_review_service, "review_against_rubric", _must_not_run
    )

    try:
        await code_review_cron_service.code_review_cron_tick(db_session_factory)
        review = await _read_review(db, result_id)
        assert review["status"] == "pending"
        assert review["attempts"] == 1
    finally:
        await _cleanup(db, result_id)
