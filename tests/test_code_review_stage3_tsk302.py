# tests/test_code_review_stage3_tsk302.py
"""
tsk-302 этап 3: язык-агностичная оценка кода моделью + фоновая очередь.

Что закрываем:
- триггер расширился с `turtle_sim` на все задания с кодом (`code_ast`), причём
  язык не важен — на проде под этой пометкой лежат и Python, и Arduino/C++;
- приём ответа больше НЕ считает синхронно, а только ставит `pending`;
- фоновый тик разбирает очередь, различает временные и постоянные сбои;
- отчёт по-прежнему не виден ученику.

Вызовы модели замоканы: тест не должен ни стоить денег, ни зависеть от сети.
Живой прогон судьи — отдельно, в артефакте ревью.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from typing import Any, Dict

import pytest
from sqlalchemy import text

from app.api.v1.attempts import _needs_code_review
from app.schemas.solution_rules import SolutionRules
from app.schemas.task_content import TaskContent
from app.services import code_review_cron_service


# ---------- Триггер: какие задания вообще идут на оценку ----------

def _rules(**kwargs) -> SolutionRules:
    return SolutionRules.model_validate({"max_score": 10, **kwargs})


def _content(type_: str = "SA_COM") -> TaskContent:
    return TaskContent.model_validate({"type": type_, "stem": "напиши программу"})


def test_trigger_covers_turtle_sim() -> None:
    """Рисование черепахой — ответ всегда программа (как было в этапе 0)."""
    rules = _rules(turtle_sim={
        "random_seed": None, "synthetic_clicks": [], "max_steps": 100,
        "timeout_sec": 5.0, "tolerance_px": 0.75,
        "expected_trace": {"segments": [], "final_state": {
            "position": [0.0, 0.0], "heading": 0.0, "pen_down": True}},
    })
    assert _needs_code_review(rules) is True


def test_trigger_covers_code_ast_tasks_regardless_of_language() -> None:
    """
    Пометка `code_ast` = «ответ сравнивается как код» (tsk-262).

    Она НЕ гарантирует Python: 40 заданий курсов «МАМ» под ней содержат
    Arduino/C++. Для нас это не важно — язык определяет сама модель, поэтому
    триггер обязан срабатывать одинаково.
    """
    rules = _rules(short_answer={
        "accepted_answers": [{"value": "print(1)", "score": 10}],
        "normalization": ["trim", "collapse_spaces", "code_ast"],
    })
    assert _needs_code_review(rules) is True


def test_trigger_ignores_plain_text_tasks() -> None:
    """Обычный текстовый ответ на оценку не идёт — незачем звать модель на сочинение."""
    rules = _rules(short_answer={
        "accepted_answers": [{"value": "Москва", "score": 10}],
        "normalization": ["trim", "lower"],
    })
    assert _needs_code_review(rules) is False
    assert _needs_code_review(_rules()) is False


# ---------- Фоновый тик ----------

async def _seed_pending(
    db, *, code: str | None, stem: str = "напиши программу", backfill: bool = False
) -> int:
    """Создаёт работу, помеченную к оценке, и возвращает её id."""
    course_id = (await db.execute(text(
        "INSERT INTO courses (title, access_level) VALUES ('tsk302 stage3', 'auto_check') RETURNING id"
    ))).scalar_one()
    task_id = (await db.execute(text(
        "INSERT INTO tasks (external_uid, max_score, task_content, solution_rules, course_id, difficulty_id) "
        "VALUES (:ext, 10, CAST(:c AS jsonb), CAST(:r AS jsonb), :cid, 1) RETURNING id"
    ), {
        "ext": f"tsk302-stage3-{random.randint(10**8, 10**10)}",
        "c": json.dumps({"type": "SA_COM", "stem": stem}),
        "r": json.dumps({"max_score": 10}),
        "cid": course_id,
    })).scalar_one()
    user_id = (await db.execute(text("SELECT id FROM users ORDER BY id LIMIT 1"))).scalar_one()
    now = datetime.now(timezone.utc)
    answer = {"type": "SA_COM", "response": {"value": code}} if code is not None else {"type": "SA_COM", "response": {}}
    result_id = (await db.execute(text(
        "INSERT INTO task_results (score, user_id, task_id, submitted_at, count_retry, received_at, "
        " max_score, source_system, answer_json, code_review) "
        "VALUES (0, :u, :t, :now, 0, :now, 10, 'test', CAST(:a AS jsonb), CAST(:cr AS jsonb)) RETURNING id"
    ), {
        "u": user_id, "t": task_id, "now": now,
        "a": json.dumps(answer),
        "cr": json.dumps({"status": "pending", **({"backfill": True} if backfill else {})}),
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


async def test_tick_writes_verdict_and_clears_pending(
    db, db_session_factory, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Успешная оценка: статус done, вердикт на месте, работа ушла из очереди."""
    result_id = await _seed_pending(db, code="x = 1\nprint(x)\n")

    async def _fake_review(code, *, task_stem=None, student_id=None):
        return {
            "language": "Python",
            "code_quality": {"score": 7, "notes": ["строка 1 — имя x ни о чём не говорит"]},
            "ai_authorship": {"verdict": "student_likely", "reasoning": "сырой стиль"},
            "model": "test-model",
        }

    monkeypatch.setattr(code_review_cron_service, "review_student_code", _fake_review)
    monkeypatch.setattr(code_review_cron_service, "analyze_student_code_quality", lambda code: None)

    try:
        summary = await code_review_cron_service.code_review_cron_tick(db_session_factory)
        assert summary["reviewed"] >= 1

        review = await _read_review(db, result_id)
        assert review["status"] == "done"
        assert review["code_quality"]["score"] == 7
        assert review["ai_authorship"]["verdict"] == "student_likely"
        assert review["language"] == "Python"
    finally:
        await _cleanup(db, result_id)


async def test_tick_keeps_pending_on_temporary_failure(
    db, db_session_factory, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Временный сбой (сеть, таймаут, остывание после 429) — работа остаётся в очереди.

    Иначе разовая сетевая ошибка навсегда лишала бы преподавателя оценки.
    """
    result_id = await _seed_pending(db, code="x = 1\nprint(x)\n")

    async def _fake_review(code, *, task_stem=None, student_id=None):
        return {"error": "LLMUnavailable", "message": "сеть", "retryable": True}

    monkeypatch.setattr(code_review_cron_service, "review_student_code", _fake_review)

    try:
        summary = await code_review_cron_service.code_review_cron_tick(db_session_factory)
        assert summary["retried"] >= 1

        review = await _read_review(db, result_id)
        assert review["status"] == "pending", "временный сбой не должен закрывать работу"
        assert review["attempts"] == 1
        assert review["last_error"] == "LLMUnavailable"
    finally:
        await _cleanup(db, result_id)


async def test_tick_gives_up_on_permanent_failure(
    db, db_session_factory, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Постоянный сбой (неверный ключ) — сразу failed, без повторов.

    Долбить провайдера на заведомо нерабочей конфигурации нельзя: это кормит
    его брейкер, который уже срабатывал в этом проекте.

    Статический анализ здесь заглушен как несработавший — это случай не-Python
    (Arduino/C++), где деградировать не на что и отчёт честно пустой. Случай с
    работающим статическим анализом проверяет `test_static_analysis_survives_model_failure`.
    """
    result_id = await _seed_pending(db, code="void loop() {\n  digitalWrite(13, HIGH);\n}\n")

    async def _fake_review(code, *, task_stem=None, student_id=None):
        return {"error": "LLMConfigError", "message": "401", "retryable": False}

    monkeypatch.setattr(code_review_cron_service, "review_student_code", _fake_review)
    monkeypatch.setattr(
        code_review_cron_service, "analyze_student_code_quality",
        lambda code: {"error": "syntax_error", "message": "не Python"},
    )

    try:
        summary = await code_review_cron_service.code_review_cron_tick(db_session_factory)
        assert summary["failed"] >= 1

        review = await _read_review(db, result_id)
        assert review["status"] == "failed"
        assert review["error"] == "LLMConfigError"
    finally:
        await _cleanup(db, result_id)


async def test_tick_skips_work_without_code(
    db, db_session_factory, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Работа без текста ответа снимается с очереди, а не крутится в ней вечно.

    Так бывает, когда ученик сдал одно вложение без текста.
    """
    result_id = await _seed_pending(db, code=None)

    async def _never_called(code, *, task_stem=None, student_id=None):
        raise AssertionError("модель не должна вызываться, когда кода нет")

    monkeypatch.setattr(code_review_cron_service, "review_student_code", _never_called)

    try:
        await code_review_cron_service.code_review_cron_tick(db_session_factory)
        review = await _read_review(db, result_id)
        assert review["status"] == "skipped"
        assert review["reason"] == "no_code"
    finally:
        await _cleanup(db, result_id)


# ---------- Находки ревью этапа 3 (2026-08-07) ----------

def test_single_line_answers_are_not_sent_to_model() -> None:
    """
    Б2: ответ-однострочник на оценку не идёт.

    На проде 49% сдач под триггером — это «допиши строку» (`HIGH`, `t.right(90)`,
    `import turtle`): сама программа лежит в условии задания. Балл «3 из 10» за
    чистоту кода слова `HIGH` хуже отсутствия оценки — преподаватель ему поверит.
    """
    from app.services.code_review_service import looks_like_program, pick_code_for_review

    assert looks_like_program("HIGH") is False
    assert looks_like_program("t.right(90)") is False
    assert looks_like_program("import turtle") is False
    assert looks_like_program("x = 1\nprint(x)") is True
    assert pick_code_for_review("HIGH", None) is None


def test_program_is_taken_from_comment_for_sa_com() -> None:
    """
    Б2: у заданий «с комментарием» программа лежит в `comment`, не в `value`.

    Реальный пример с прода: `value='digitalRead'`, а в комментарии —
    `int sostoyanie = digitalRead(2);`. Читать только `value` значит оценивать
    не то, что писал ученик.
    """
    from app.services.code_review_service import pick_code_for_review

    picked = pick_code_for_review("digitalWrite", "digitalWrite(13, HIGH);\ndelay(200);\n")
    assert picked is not None
    assert "delay(200)" in picked


async def test_static_analysis_survives_model_failure(
    db, db_session_factory, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Б1: при недоступной модели фича ДЕГРАДИРУЕТ до статического анализа, а не исчезает.

    На проде ключа модели ещё нет, и первая редакция этапа 3 писала бы `failed`
    вместо работавшего pylint-отчёта — то есть выкат отобрал бы у преподавателя
    то, что уже работало (этап 0).
    """
    result_id = await _seed_pending(db, code="x = 1\nprint(x)\n")

    async def _model_down(code, *, task_stem=None, student_id=None):
        return {"error": "LLMConfigError", "message": "нет ключа", "retryable": False}

    monkeypatch.setattr(code_review_cron_service, "review_student_code", _model_down)
    monkeypatch.setattr(
        code_review_cron_service, "analyze_student_code_quality",
        lambda code: {"pylint": {"score": 8.5, "messages": []}, "radon": {"complexity": []}},
    )

    try:
        await code_review_cron_service.code_review_cron_tick(db_session_factory)
        review = await _read_review(db, result_id)

        assert review["status"] == "done", "статический анализ есть — отчёт не пустой"
        assert review["degraded"] is True, "видно, что оценка неполная"
        assert review["static"]["pylint"]["score"] == 8.5
        assert review["error"] == "LLMConfigError"
    finally:
        await _cleanup(db, result_id)


def test_user_message_contains_lowercase_json_keyword() -> None:
    """
    Слово «json» строчными обязано быть в ПОЛЬЗОВАТЕЛЬСКОМ сообщении.

    Требование OpenAI-совместимых провайдеров при `response_format=json_object`:
    без него приходит HTTP 400 «Response input messages must contain the word
    'json' in some form». Поймано живым прогоном на проде 2026-08-07 — в промпте
    было «объектом JSON» заглавными, и 4 работы из 7 в пересчёте отвалились с
    `LLMMalformed`. Проверка именно на user-сообщении: наличия слова в системном
    промпте провайдеру НЕ хватает, это тоже проверено живьём.
    """
    from app.services.code_review_service import _build_user_message

    message = _build_user_message("x = 1\nprint(x)", task_stem="условие")
    assert "json" in message, (
        "провайдер отвергнет запрос с response_format=json_object, если в "
        "пользовательском сообщении нет слова 'json' строчными"
    )


def test_parse_verdict_survives_model_quirks() -> None:
    """
    Н8: разбор ответа модели не должен падать на предсказуемых причудах.

    Модель иногда оборачивает JSON в ```-забор вопреки инструкции, а вердикт
    может прийти неизвестным. Терять из-за этого весь отчёт — расточительно,
    а выдумывать обвинение из мусора — опасно.
    """
    from app.services.code_review_service import _parse_verdict

    fenced = _parse_verdict(
        '```json\n{"language":"Python","code_quality":{"score":7,"notes":["a"]},'
        '"ai_authorship":{"verdict":"student_likely","reasoning":"r"}}\n```'
    )
    assert fenced["code_quality"]["score"] == 7
    assert fenced["ai_authorship"]["verdict"] == "student_likely"

    # Неизвестный вердикт трактуется как «сигнала нет», а не как обвинение.
    unknown = _parse_verdict('{"ai_authorship":{"verdict":"definitely_cheating"}}')
    assert unknown["ai_authorship"]["verdict"] == "ambiguous"

    # Балл вне шкалы подрезается, а не уезжает на экран как есть.
    out_of_range = _parse_verdict('{"code_quality":{"score":99}}')
    assert out_of_range["code_quality"]["score"] == 10


# ---------- Инвариант видимости ----------

def test_stage3_report_still_hidden_from_student() -> None:
    """
    Новые секции отчёта не должны просочиться в ответ ученику на сдачу.

    Страж на всю цепочку уже есть в test_code_quality_tsk302, здесь проверяем,
    что этап 3 не завёл в ученических схемах поля под свои имена.
    """
    from app.schemas.attempts import AttemptAnswerResult, AttemptAnswersResponse
    from app.schemas.checking import CheckResult

    for schema in (AttemptAnswersResponse, AttemptAnswerResult, CheckResult):
        for field_name in schema.model_fields:
            lowered = field_name.lower()
            assert "authorship" not in lowered, (
                f"{schema.__name__}.{field_name} утекает признак ИИ-авторства ученику"
            )
            assert "code_review" not in lowered


async def test_backfill_marker_survives_the_report(
    db, db_session_factory, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Работа, попавшая в очередь пересчётом, остаётся помеченной и после оценки.

    Отчёт пишется целиком, поэтому без явного переноса метка терялась бы на
    первом же тике — и потом нечем было бы отделить оценки старых работ от
    оценок живых сдач (находка ревью 2026-08-07).
    """
    result_id = await _seed_pending(db, code="x = 1\nprint(x)\n", backfill=True)

    async def _fake_review(code, *, task_stem=None, student_id=None):
        return {"language": "Python", "code_quality": {"score": 7}, "model": "test-model"}

    monkeypatch.setattr(code_review_cron_service, "review_student_code", _fake_review)
    monkeypatch.setattr(code_review_cron_service, "analyze_student_code_quality", lambda code: None)

    try:
        await code_review_cron_service.code_review_cron_tick(db_session_factory)
        review = await _read_review(db, result_id)
        assert review["status"] == "done"
        assert review["backfill"] is True
    finally:
        await _cleanup(db, result_id)


async def test_live_submission_is_not_marked_as_backfill(
    db, db_session_factory, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Обратная сторона: живой сдаче метка не приписывается ни при каких условиях."""
    result_id = await _seed_pending(db, code="x = 1\nprint(x)\n")

    async def _fake_review(code, *, task_stem=None, student_id=None):
        return {"language": "Python", "code_quality": {"score": 7}, "model": "test-model"}

    monkeypatch.setattr(code_review_cron_service, "review_student_code", _fake_review)
    monkeypatch.setattr(code_review_cron_service, "analyze_student_code_quality", lambda code: None)

    try:
        await code_review_cron_service.code_review_cron_tick(db_session_factory)
        assert "backfill" not in (await _read_review(db, result_id))
    finally:
        await _cleanup(db, result_id)
