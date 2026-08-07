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

from app.services import code_review_cron_service


# ---------- Отбор: какие РАБОТЫ идут на оценку ----------
#
# Раньше отбор шёл от пометки у задания (`turtle_sim` / `code_ast`). Прод-данные
# показали, что это неверно: у заданий реального курса пометки нет, а код лежит
# во вложении или в комментарии. Признак теперь берётся из самой работы.


def test_program_in_answer_is_picked() -> None:
    """Код прямо в поле ответа — работа идёт на оценку."""
    from app.services.code_review_service import pick_code_for_review

    assert pick_code_for_review("x = 1\nprint(x)", None, attempt_id=None) == "x = 1\nprint(x)"


def test_program_in_comment_is_picked_without_any_task_flag() -> None:
    """
    Код в комментарии подхватывается, даже если у задания нет пометки.

    Это самый частый случай на проде: 370 работ с комментарием, оценку получили
    5. Ученик пишет в ответ результат (`131`), а программу — в комментарий.
    """
    from app.services.code_review_service import pick_code_for_review

    comment = (
        "m = int(input('Количество туристов: '))\n"
        "mot = (m + 1) // 2\n"
        "print(mot, 'Понадобится мотоциклов')"
    )
    assert pick_code_for_review("131", comment, attempt_id=None) == comment


def test_prose_is_not_sent_to_the_model() -> None:
    """
    Рассуждение ученика — не код, и модели его отдавать нельзя.

    Иначе преподаватель получит «оценку чистоты кода» сочинения и перестанет
    доверять всей затее. Примеры взяты из реальных комментариев прода.
    """
    from app.services.code_review_service import pick_code_for_review

    real_prose = [
        "В сообщении присутствуют 2 типа кода: 0 и 11, если бы в нём была\nещё одна цифра",
        "по горизонтали идем от столбца 1 до вертикального столбца 4,\nчтобы получить ответ",
        "без комментария не дает отправить на проверку\nпоэтому пишу сюда",
        "запрос: напиши код для сайта на языке html\nДелался: 30 секунд",
        "101101\n1101110",
    ]
    for text_ in real_prose:
        assert pick_code_for_review(None, text_, attempt_id=None) is None, text_[:40]


def test_single_line_answer_is_not_picked() -> None:
    """Ответ-однострочник «допиши строку» на оценку не идёт (находка ревью Б2)."""
    from app.services.code_review_service import pick_code_for_review

    for one_liner in ("HIGH", "t.right(90)", "import turtle", "131"):
        assert pick_code_for_review(one_liner, None, attempt_id=None) is None, one_liner


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
    assert pick_code_for_review("HIGH", None, attempt_id=None) is None


def test_program_is_taken_from_comment_for_sa_com() -> None:
    """
    Б2: у заданий «с комментарием» программа лежит в `comment`, не в `value`.

    Реальный пример с прода: `value='digitalRead'`, а в комментарии —
    `int sostoyanie = digitalRead(2);`. Читать только `value` значит оценивать
    не то, что писал ученик.
    """
    from app.services.code_review_service import pick_code_for_review

    picked = pick_code_for_review(
        "digitalWrite", "digitalWrite(13, HIGH);\ndelay(200);\n", attempt_id=None
    )
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


# ─── Код во вложении (самый частый формат реального курса) ──────────────────


def test_code_is_taken_from_attachment(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Программа берётся из приложенного файла, когда в ответе — ВЫВОД программы.

    Формат «приложи task8.py, а в поле ответа впиши, что программа напечатала»
    — самый частый в реальном курсе: на проде 101 такая работа у 8 учеников,
    и до этой правки ни одна не получила оценки. Пометки `code_ast`/`turtle_sim`
    у таких заданий нет, а вывод (`1 / 22 / 333`) на программу не похож, так
    что ни один прежний путь их не подбирал.
    """
    from app.services import code_review_service as svc

    upload = tmp_path / "attachments"
    upload.mkdir()
    (upload / "12336_t7_0c845dc488de4a0d87fdf05a60d0644d_task8.py").write_text(
        "for i in range(1, 6):\n    print(str(i) * i)\n", encoding="utf-8"
    )
    monkeypatch.setenv("ATTEMPT_ATTACHMENTS_UPLOAD_DIR", str(upload))

    attachments = [{"filename": "task8.py", "attachment_id": "12336_t7_0c845dc488de4a0d87fdf05a60d0644d_task8.py"}]
    code = svc.pick_code_for_review(
        "1\n22\n333\n4444\n55555", None, attachments, attempt_id=12336, task_id=7
    )

    assert code is not None, "код должен приехать из вложения"
    assert "range(1, 6)" in code
    assert "22" not in code.splitlines()[0], "это должен быть код, а не вывод программы"


def test_attachment_wins_over_text_answer(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Файл-исходник главнее текста ответа — и это не вкусовщина.

    Вывод программы из нескольких строк САМ проходит порог «похоже на
    программу»: читай мы сначала текст, на оценку уехал бы столбик цифр вместо
    кода, а преподаватель получил бы разбор «чистоты» этого столбика. Файл —
    буквально то, что ученик написал, поэтому он и первый.
    """
    from app.services import code_review_service as svc

    upload = tmp_path / "attachments"
    upload.mkdir()
    (upload / "1_t7_0c845dc488de4a0d87fdf05a60d0644d_task.py").write_text(
        "for i in range(3):\n    print(i)\n", encoding="utf-8"
    )
    monkeypatch.setenv("ATTEMPT_ATTACHMENTS_UPLOAD_DIR", str(upload))

    # В поле ответа — вывод программы, а не код.
    code = svc.pick_code_for_review(
        "0\n1\n2", None, [{"filename": "task.py", "attachment_id": "1_t7_0c845dc488de4a0d87fdf05a60d0644d_task.py"}],
        attempt_id=1, task_id=7
    )
    assert code is not None and "range(3)" in code


def test_text_answer_used_when_no_code_attachment(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Без файла-исходника работает прежний путь: код из ответа или комментария."""
    from app.services import code_review_service as svc

    upload = tmp_path / "attachments"
    upload.mkdir()
    monkeypatch.setenv("ATTEMPT_ATTACHMENTS_UPLOAD_DIR", str(upload))

    assert svc.pick_code_for_review(
        "x = 1\nprint(x)", None, None, attempt_id=None
    ) == "x = 1\nprint(x)"
    assert svc.pick_code_for_review(
        "digitalRead", "int s = digitalRead(2);\nSerial.println(s);", [], attempt_id=None
    ).startswith("int s")


def test_non_code_attachments_are_ignored(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Картинка или архив — не повод звать модель.

    Список расширений закрытый намеренно: отдавать модели произвольный файл
    ученика — риск без пользы, а к заданиям тут прикладывают и скриншоты.
    """
    from app.services import code_review_service as svc

    upload = tmp_path / "attachments"
    upload.mkdir()
    monkeypatch.setenv("ATTEMPT_ATTACHMENTS_UPLOAD_DIR", str(upload))

    for filename in ("screenshot.png", "otchet.docx", "arhiv.zip", "dannye.csv"):
        assert svc.pick_code_attachment([{"filename": filename, "attachment_id": "x"}]) is None
        # Ответ-однострочник + не-кодовое вложение = оценивать нечего.
        assert svc.pick_code_for_review(
            "21", None, [{"filename": filename}], attempt_id=1, task_id=7
        ) is None


def test_unreadable_attachment_does_not_break_review(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Файла нет, он бинарный или слишком большой — работа просто не оценивается.

    Падать нельзя: этот код выполняется в приёме ответа ученика, и нечитаемый
    файл не должен мешать ему сдать задание.
    """
    from app.services import code_review_service as svc

    upload = tmp_path / "attachments"
    upload.mkdir()
    (upload / "1_t7_0c845dc488de4a0d87fdf05a60d0644d_binary.py").write_bytes(b"\xff\xfe\x00\x01binary")
    (upload / "1_t7_0c845dc488de4a0d87fdf05a60d0644d_huge.py").write_text("x = 1\n" * 20000, encoding="utf-8")
    monkeypatch.setenv("ATTEMPT_ATTACHMENTS_UPLOAD_DIR", str(upload))

    for attachment_id in ("1_t7_0c845dc488de4a0d87fdf05a60d0644d_missing.py", "1_t7_0c845dc488de4a0d87fdf05a60d0644d_binary.py", "1_t7_0c845dc488de4a0d87fdf05a60d0644d_huge.py"):
        assert svc.read_code_attachment(attachment_id, attempt_id=1, task_id=7) is None, attachment_id
        assert svc.pick_code_for_review(
            "вывод", None, [{"filename": "f.py", "attachment_id": attachment_id}],
            attempt_id=1, task_id=7
        ) is None


def test_attachment_path_cannot_escape_upload_dir(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Имя вложения приходит из JSONB — выход из каталога загрузок закрыт."""
    from app.services import code_review_service as svc

    upload = tmp_path / "attachments"
    upload.mkdir()
    (tmp_path / "secret.py").write_text("SECRET = 1\nprint(SECRET)\n", encoding="utf-8")
    monkeypatch.setenv("ATTEMPT_ATTACHMENTS_UPLOAD_DIR", str(upload))

    assert svc.read_code_attachment("../secret.py", attempt_id=1, task_id=7) is None


def test_code_attachment_covers_popular_languages() -> None:
    """
    Охват не «только Python»: язык определяет сама модель.

    Список расширений закрыт (произвольный файл ученика модели не отдаём), но
    внутри него — все ходовые языки, включая школьные ЕГЭ/ОГЭ: Pascal, КуМир,
    Basic. Ограничивать проверку одним языком нет причин — оценивает её одна и
    та же модель, а курсы уже сейчас разноязычные (Python и Arduino/C++).
    """
    from app.services.code_review_service import looks_like_code_attachment

    should_match = [
        "solution.py", "notebook.ipynb",              # Python
        "main.cpp", "lib.h", "sketch.ino", "prog.c",  # C-семейство и Arduino
        "App.java", "Main.kt", "Program.cs",          # JVM и .NET
        "script.js", "app.ts", "index.php",           # веб
        "server.go", "lib.rs", "app.swift", "run.rb", # системные
        "zadacha.pas", "algoritm.kum", "prog.bas",    # школьные (ЕГЭ/ОГЭ)
        "analiz.r", "raschet.m", "query.sql",         # научные и запросы
    ]
    for filename in should_match:
        assert looks_like_code_attachment(filename), f"{filename} должен считаться кодом"

    for filename in ("foto.jpg", "otchet.pdf", "dannye.csv", "arhiv.rar", "zametki.txt"):
        assert not looks_like_code_attachment(filename), f"{filename} кодом не является"


def test_notebook_gives_code_not_json(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Из тетради Jupyter берём исходники ячеек, а не JSON целиком.

    Иначе программа утонула бы в служебной разметке и выводах ячеек, и модель
    оценивала бы формат файла вместо кода ученика.
    """
    import json as _json

    from app.services import code_review_service as svc

    upload = tmp_path / "attachments"
    upload.mkdir()
    notebook = {
        "cells": [
            {"cell_type": "markdown", "source": ["# Заголовок\n"]},
            {"cell_type": "code", "source": ["x = 1\n", "print(x)\n"],
             "outputs": [{"text": ["1\n"]}]},
            {"cell_type": "code", "source": "y = 2\nprint(y)\n"},
        ],
        "metadata": {"kernelspec": {"name": "python3"}},
    }
    (upload / "1_t7_0c845dc488de4a0d87fdf05a60d0644d_rabota.ipynb").write_text(_json.dumps(notebook), encoding="utf-8")
    monkeypatch.setenv("ATTEMPT_ATTACHMENTS_UPLOAD_DIR", str(upload))

    code = svc.read_code_attachment("1_t7_0c845dc488de4a0d87fdf05a60d0644d_rabota.ipynb", attempt_id=1, task_id=7)
    assert code is not None
    assert "print(x)" in code and "print(y)" in code
    assert "cell_type" not in code and "kernelspec" not in code
    assert "# Заголовок" not in code, "текстовые ячейки — не код"


async def test_tick_uses_code_snapshot_when_file_is_gone(
    db, db_session_factory, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Тик берёт снимок кода, снятый при приёме ответа, а не перечитывает файл.

    Файл вложения ИЗМЕНЯЕМ: повторная загрузка по той же паре (попытка,
    задание) вытесняет прежний. Читай тик файл сам — он взял бы уже другую
    редакцию решения и приписал её этой сдаче, а на исторических работах и
    вовсе пустоту: из 101 работы со ссылкой на `.py` файлы уцелели лишь у 8
    (файлы терялись до починки tsk-575).
    """
    result_id = await _seed_pending(db, code=None)
    await db.execute(text(
        "UPDATE task_results SET code_review = CAST(:cr AS jsonb), "
        "answer_json = CAST(:a AS jsonb) WHERE id = :r"
    ), {
        "cr": json.dumps({"status": "pending", "code": "for i in range(3):\n    print(i)\n"}),
        # В ответе — вывод программы; вложение указывает на уже удалённый файл.
        "a": json.dumps({"type": "SA_COM", "response": {
            "value": "0\n1\n2",
            "meta": {"attachments": [
                {"filename": "task.py", "attachment_id": "999_t7_0c845dc488de4a0d87fdf05a60d0644d_task.py"}
            ]},
        }}),
        "r": result_id,
    })
    await db.commit()

    seen: Dict[str, Any] = {}

    async def _fake_review(code, *, task_stem=None, student_id=None):
        seen["code"] = code
        return {"language": "Python", "code_quality": {"score": 6}, "model": "test-model"}

    monkeypatch.setattr(code_review_cron_service, "review_student_code", _fake_review)
    monkeypatch.setattr(code_review_cron_service, "analyze_student_code_quality", lambda code: None)

    try:
        await code_review_cron_service.code_review_cron_tick(db_session_factory)
        assert "range(3)" in seen.get("code", ""), "оценивать надо код из снимка"
        assert "0\n1\n2" != seen.get("code"), "вывод программы вместо кода — дефект"
        review = await _read_review(db, result_id)
        assert review["status"] == "done"
        assert "code" not in review, "снимок временный, в готовый отчёт он не идёт"
    finally:
        await _cleanup(db, result_id)


async def test_snapshot_survives_retry(
    db, db_session_factory, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Повтор после временного сбоя не должен терять снимок кода."""
    result_id = await _seed_pending(db, code=None)
    await db.execute(text(
        "UPDATE task_results SET code_review = CAST(:cr AS jsonb) WHERE id = :r"
    ), {"cr": json.dumps({"status": "pending", "code": "x = 1\nprint(x)\n"}), "r": result_id})
    await db.commit()

    async def _fake_review(code, *, task_stem=None, student_id=None):
        return {"error": "LLMUnavailable", "message": "сеть", "retryable": True}

    monkeypatch.setattr(code_review_cron_service, "review_student_code", _fake_review)
    monkeypatch.setattr(code_review_cron_service, "analyze_student_code_quality", lambda code: None)

    try:
        await code_review_cron_service.code_review_cron_tick(db_session_factory)
        review = await _read_review(db, result_id)
        assert review["status"] == "pending"
        assert "print(x)" in review.get("code", ""), "без снимка повтор оценивать нечего"
    finally:
        await _cleanup(db, result_id)


# ─── Данные ученика не должны ронять приём ответа ───────────────────────────


def test_hostile_attachment_meta_does_not_raise(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    `meta.attachments` приходит из ТЕЛА ЗАПРОСА и схемой не проверяется.

    Ревью 2026-08-07 воспроизвело по сети: `{"filename": 5}` роняло разбор
    внутри эндпоинта, то есть ученик не мог сдать задание вообще. Оценка кода —
    побочная фича, сдача — основная, поэтому здесь не может быть исключений
    ни при каких данных.
    """
    from app.services import code_review_service as svc

    monkeypatch.setenv("ATTEMPT_ATTACHMENTS_UPLOAD_DIR", str(tmp_path))

    hostile = [
        [{"filename": 5}],
        [{"filename": ["a", "b"]}],
        [{"filename": "task.py", "attachment_id": 42}],
        [{"filename": "task.py", "attachment_id": {"a": 1}}],
        ["строка вместо словаря"],
        [None],
        {"не": "список"},
        42,
        "строка",
    ]
    for attachments in hostile:
        # Проверяем ВНУТРЕННЮЮ функцию тоже: иначе тест удовлетворялся бы одним
        # широким `except` в обёртке, и настоящая дыра осталась бы незакрытой.
        assert svc._pick_code_for_review("21", None, attachments, 1, 7, False) is None, repr(attachments)
        assert svc.pick_code_for_review(
            "21", None, attachments, attempt_id=1, task_id=7
        ) is None, repr(attachments)


def test_hostile_notebook_does_not_raise(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Содержимое тетради целиком задаёт ученик — там может лежать что угодно.

    `{"cells": [{"cell_type": "code", "source": [1, 2]}]}` — валидный JSON и
    валидные метаданные, но склейка такого списка роняла разбор.
    """
    import json as _json

    from app.services import code_review_service as svc

    upload = tmp_path / "attachments"
    upload.mkdir()
    monkeypatch.setenv("ATTEMPT_ATTACHMENTS_UPLOAD_DIR", str(upload))

    U32 = "0c845dc488de4a0d87fdf05a60d0644d"
    hostile_notebooks = [
        {"cells": [{"cell_type": "code", "source": [1, 2]}]},
        {"cells": [{"cell_type": "code", "source": {"а": "б"}}]},
        {"cells": "не список"},
        {"cells": [None, 5, "строка"]},
        {"нет": "ячеек"},
        [1, 2, 3],
    ]
    for idx, notebook in enumerate(hostile_notebooks):
        name = f"1_t7_{U32}_rabota{idx}.ipynb"
        (upload / name).write_text(_json.dumps(notebook), encoding="utf-8")
        assert svc.pick_code_for_review(
            "21", None, [{"filename": "rabota.ipynb", "attachment_id": name}],
            attempt_id=1, task_id=7
        ) is None, repr(notebook)

    # Не JSON вовсе — тоже не повод падать.
    (upload / "1_t7_0c845dc488de4a0d87fdf05a60d0644d_broken.ipynb").write_text("это не json", encoding="utf-8")
    assert svc.read_code_attachment("1_t7_0c845dc488de4a0d87fdf05a60d0644d_broken.ipynb", attempt_id=1, task_id=7) is None


def test_pick_code_never_raises_even_on_broken_internals(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Обещание «исключений не бросает» держится буквально, а не на честном слове.

    Ломаем внутренности намеренно: если однажды в разборе появится новая
    ошибка, приём ответа ученика об этом знать не должен.
    """
    from app.services import code_review_service as svc

    def _boom(*args, **kwargs):
        raise RuntimeError("внутренняя поломка разбора")

    monkeypatch.setattr(svc, "iter_code_attachments", _boom)
    assert svc.pick_code_for_review("x = 1\nprint(x)", None, None, attempt_id=None) is None


# ─── Подмена вложения: имя файла приходит из тела запроса ────────────────────


def test_attachment_from_another_attempt_is_refused(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Файл чужой попытки не читается, даже если он существует и это код.

    `attachment_id` приходит в `meta.attachments`, то есть из ТЕЛА ЗАПРОСА —
    в приёме ответа рядом записан прямой запрет доверять этому полю. Без
    сверки с попыткой ученик подставил бы `attachment_id` своей вылизанной
    работы от другого задания и получил бы по ней и оценку чистоты, и вердикт
    детектора ИИ. Для фичи, которая существует ради выявления списывания, это
    обход в один шаг (находка ревью 2026-08-07).
    """
    from app.services import code_review_service as svc

    upload = tmp_path / "attachments"
    upload.mkdir()
    (upload / "777_t7_0c845dc488de4a0d87fdf05a60d0644d_horoshiy.py").write_text(
        "def solve(n):\n    return n * 2\n", encoding="utf-8"
    )
    monkeypatch.setenv("ATTEMPT_ATTACHMENTS_UPLOAD_DIR", str(upload))

    # Своя попытка — читаем.
    assert svc.read_code_attachment("777_t7_0c845dc488de4a0d87fdf05a60d0644d_horoshiy.py", attempt_id=777, task_id=7) is not None
    # Чужая — нет, хотя файл на месте и это настоящий код.
    assert svc.read_code_attachment("777_t7_0c845dc488de4a0d87fdf05a60d0644d_horoshiy.py", attempt_id=778, task_id=7) is None
    # Попытка не указана — тоже нет: отказ безопаснее догадки.
    assert svc.read_code_attachment(
        "777_t7_0c845dc488de4a0d87fdf05a60d0644d_horoshiy.py", attempt_id=None, task_id=7
    ) is None

    attachments = [{"filename": "horoshiy.py", "attachment_id": "777_t7_0c845dc488de4a0d87fdf05a60d0644d_horoshiy.py"}]
    assert svc.pick_code_for_review("21", None, attachments, attempt_id=778, task_id=7) is None
    assert svc.pick_code_for_review("21", None, attachments, attempt_id=777, task_id=7) is not None


def test_real_extension_wins_over_declared_filename(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Расширение проверяется у ФАЙЛА НА ДИСКЕ, а не у заявленного имени.

    Иначе закрытый список расширений не закрывает ничего: `filename` тоже
    приходит из тела запроса, и таблицу можно назвать `moe.py`, чтобы её
    прочитали и отдали модели.
    """
    from app.services import code_review_service as svc

    upload = tmp_path / "attachments"
    upload.mkdir()
    (upload / "5_t7_0c845dc488de4a0d87fdf05a60d0644d_dannye.csv").write_text("имя;балл\nИван;5\nПётр;4\n", encoding="utf-8")
    monkeypatch.setenv("ATTEMPT_ATTACHMENTS_UPLOAD_DIR", str(upload))

    assert svc.read_code_attachment("5_t7_0c845dc488de4a0d87fdf05a60d0644d_dannye.csv", attempt_id=5, task_id=7) is None
    assert svc.pick_code_for_review(
        "21", None,
        [{"filename": "moe.py", "attachment_id": "5_t7_0c845dc488de4a0d87fdf05a60d0644d_dannye.csv"}],
        attempt_id=5, task_id=7,
    ) is None


def test_prefix_check_is_not_fooled_by_similar_ids(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Попытка 7 не должна получать доступ к файлам попытки 77."""
    from app.services import code_review_service as svc

    upload = tmp_path / "attachments"
    upload.mkdir()
    (upload / "77_t7_0c845dc488de4a0d87fdf05a60d0644d_task.py").write_text("x = 1\nprint(x)\n", encoding="utf-8")
    monkeypatch.setenv("ATTEMPT_ATTACHMENTS_UPLOAD_DIR", str(upload))

    assert svc.read_code_attachment("77_t7_0c845dc488de4a0d87fdf05a60d0644d_task.py", attempt_id=7, task_id=7) is None
    assert svc.read_code_attachment("77_t7_0c845dc488de4a0d87fdf05a60d0644d_task.py", attempt_id=77, task_id=7) is not None


def test_formula_calculation_is_not_code() -> None:
    """
    Расчёт по формуле — не программа, даже если строки похожи на присваивания.

    `S = a * b` / `V = S * h` — это ход решения задачи по математике, который
    ученик записал в комментарий. Одних присваиваний мало: нужен явный признак
    (вызов, ключевое слово, структура). Проверено на прод-выборке — ужесточение
    стоило 2 работы из 269, обе как раз такого вида.
    """
    from app.services.code_review_service import looks_like_source_code

    assert looks_like_source_code("S = a * b\nV = S * h") is False
    assert looks_like_source_code("x = 5\ny = 10\nz = x + y") is False

    # А настоящая программа с теми же присваиваниями — проходит.
    assert looks_like_source_code("x = 5\ny = 10\nprint(x + y)") is True


def test_attachment_of_another_task_is_refused(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Файл ДРУГОГО задания той же попытки не читается.

    Это исходный вектор находки ревью: попытка охватывает много заданий, и
    ученик мог бы подставить один вылизанный `solution.py` в задания 2..N.
    Сверка попытки такое не ловит — нужна метка задания в имени файла.
    """
    from app.services import code_review_service as svc

    upload = tmp_path / "attachments"
    upload.mkdir()
    name = "10_t5_0c845dc488de4a0d87fdf05a60d0644d_solution.py"
    (upload / name).write_text("def solve(n):\n    return n * 2\n", encoding="utf-8")
    monkeypatch.setenv("ATTEMPT_ATTACHMENTS_UPLOAD_DIR", str(upload))

    # Своё задание — читаем.
    assert svc.read_code_attachment(name, attempt_id=10, task_id=5) is not None
    # Соседнее задание той же попытки — нет.
    assert svc.read_code_attachment(name, attempt_id=10, task_id=6) is None

    attachments = [{"filename": "solution.py", "attachment_id": name}]
    assert svc.pick_code_for_review("21", None, attachments, attempt_id=10, task_id=6) is None
    assert svc.pick_code_for_review("21", None, attachments, attempt_id=10, task_id=5) is not None


def test_untagged_attachment_is_refused_on_live_path(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Файл БЕЗ метки задания на живом пути не читается вовсе.

    Формат имени выбирает не сервер: приём вложений принимает загрузку без
    `task_id` (ради старых клиентов), а грузит файл сам ученик. Значит он мог
    бы получить безметочный файл намеренно и тем обойти сверку задания —
    находка ревью 2026-08-07. Историю разбирает только скрипт пересчёта,
    явным `allow_untagged=True`: задним числом ученик её не перезальёт.
    """
    from app.services import code_review_service as svc

    upload = tmp_path / "attachments"
    upload.mkdir()
    name = "10_0c845dc488de4a0d87fdf05a60d0644d_solution.py"
    (upload / name).write_text("def solve(n):\n    return n * 2\n", encoding="utf-8")
    monkeypatch.setenv("ATTEMPT_ATTACHMENTS_UPLOAD_DIR", str(upload))

    assert svc.read_code_attachment(name, attempt_id=10, task_id=5) is None
    assert svc.read_code_attachment(
        name, attempt_id=10, task_id=5, allow_untagged=True
    ) is not None

    attachments = [{"filename": "solution.py", "attachment_id": name}]
    assert svc.pick_code_for_review("21", None, attachments, attempt_id=10, task_id=5) is None
    assert svc.pick_code_for_review(
        "21", None, attachments, attempt_id=10, task_id=5, allow_untagged=True
    ) is not None
