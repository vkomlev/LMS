# tests/test_criteria_review_tsk958.py
"""
tsk-958: судья по критериям для коротких ответов `SA`/`SA_COM`.

Что закрываем:
- из `grading_criteria` в промпт идут все четыре части: `must` — пунктами,
  `reject` — отдельными пунктами «есть ли это в ответе», `accept` и `notes` —
  контекстом; у рубрики TA промпт остаётся прежним;
- итог считает КОД и он бинарный: все `must` выполнены и ни один `reject` не
  сработал → `pass`; хоть один `must` не выполнен либо сработал `reject` →
  `fail`; «не видно» → `unclear`. Вердикт, присланный моделью, игнорируется;
- текст для судьи собирается из ОБОИХ полей ответа (форма зависит от задания),
  программа из вложения добавляется, если не повторяет поля;
- приём ответа помечает работу к разбору только при включённом рубильнике
  школы и только у задания с подтверждёнными критериями и без эталона; при
  найденной программе разбор идёт вдобавок к оценке кода (`rubric: true`);
- фоновый тик разбирает работу вида `criteria` одним вызовом модели (без
  признака авторства), уважает выключенный рубильник и кладёт разбор рядом
  с оценкой кода у кодовых работ;
- расход учитывается своим назначением (`criteria_review`);
- ученику разбор не виден: ответ на сдачу не несёт `code_review`.

Вызовы модели замоканы. Калибровка на живых сдачах — отдельно,
docs/qa/2026-09-15-tsk958-criteria-judge-calibration.md.
"""
from __future__ import annotations

import json
import os
import random
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from app.api.deps import get_current_user  # noqa: E402
from app.api.main import app  # noqa: E402
from app.auth.current_user import CurrentUser  # noqa: E402
from app.core import settings_store  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.schemas.code_review import CodeReviewReport  # noqa: E402
from app.services import code_review_cron_service, rubric_review_service  # noqa: E402

_settings = Settings()

_CRITERIA_RULES: Dict[str, Any] = {
    "max_score": 1,
    "manual_review_required": True,
    "grading_criteria": {
        "must": [
            "Программа читает количество чисел, а затем сами числа",
            "Максимум ищется среди чисел, кратных 5, а не среди всех",
        ],
        "accept": ["Любые имена переменных и любой способ ввода чисел"],
        "reject": ["Максимум ищется среди всех чисел без проверки кратности"],
        "notes": "Ученик сдаёт программу в комментарии, в поле ответа — её вывод.",
        "status": "approved",
        "reviewed_by": 2,
    },
}

_TA_RULES: Dict[str, Any] = {
    "max_score": 3,
    "text_answer": {
        "auto_check": False,
        "rubric": [
            {"id": "c1", "title": "Выписаны команды прибора", "max_score": 1},
            {"id": "c2", "title": "Есть вывод о формальности исполнителя", "max_score": 2},
        ],
    },
    "manual_review_required": True,
}

_PROGRAM = (
    "n = int(input())\n"
    "best = None\n"
    "for i in range(n):\n"
    "    x = int(input())\n"
    "    if x % 5 == 0 and (best is None or x > best):\n"
    "        best = x\n"
    "print(best)\n"
)

_JUDGEMENT = (
    "Считаю, что тест-кейс неполный: в нём нет ожидаемого результата и "
    "предусловий, а шаги описаны так, что их нельзя повторить другому человеку. "
    "Я бы добавил столбец «ожидаемый результат» и указал версию приложения."
)


def _fake_result(payload: Dict[str, Any]) -> Any:
    return type("R", (), {"text": json.dumps(payload, ensure_ascii=False), "model": "test-model"})()


@pytest.fixture
def criteria_enabled(monkeypatch: pytest.MonkeyPatch):
    """Рубильник школы включён на время теста — по умолчанию он выключен (tsk-958)."""
    monkeypatch.setitem(settings_store._cabinet, "criteria_review_enabled", True)
    yield


# ───────────────────────── Сборка промпта из критериев ───────────────────────


def test_spec_carries_must_reject_accept_and_notes() -> None:
    spec = rubric_review_service.rubric_spec(_CRITERIA_RULES)
    assert spec is not None
    assert spec["source"] == "grading_criteria"
    assert [(i["id"], i["kind"]) for i in spec["items"]] == [
        ("c1", "must"), ("c2", "must"), ("r1", "reject"),
    ]
    assert spec["accept"] == ["Любые имена переменных и любой способ ввода чисел"]
    assert spec["notes"].startswith("Ученик сдаёт программу")
    # Прежний короткий доступ к пунктам работает и отдаёт те же три пункта.
    assert len(rubric_review_service.rubric_items(_CRITERIA_RULES)) == 3


def test_prompt_has_reject_and_accept_sections_for_criteria() -> None:
    spec = rubric_review_service.rubric_spec(_CRITERIA_RULES)
    message = rubric_review_service._build_user_message(
        "Ответ:\n25", task_stem="Найди максимум", items=spec["items"],
        accept=spec["accept"], notes=spec["notes"],
    )
    assert "Что НЕ засчитывать" in message
    assert "- r1: Максимум ищется среди всех чисел" in message
    assert "Что засчитывать наравне:\n- Любые имена переменных" in message
    assert "Пояснение проверяющему:\nУченик сдаёт программу" in message
    # `reject` не попал в список обязательных пунктов.
    must_block = message.split("Критерии проверки:\n", 1)[1].split("\n\n", 1)[0]
    assert "r1" not in must_block


def test_prompt_for_ta_rubric_is_unchanged() -> None:
    """У рубрики TA нет ни `reject`, ни `accept` — лишних секций в промпте нет."""
    spec = rubric_review_service.rubric_spec(_TA_RULES)
    message = rubric_review_service._build_user_message(
        "текст", task_stem="Опиши", items=spec["items"],
        accept=spec["accept"], notes=spec["notes"],
    )
    assert "Что НЕ засчитывать" not in message
    assert "Что засчитывать наравне" not in message
    assert "Пояснение проверяющему" not in message


# ───────────────────────────── Итог считает код ──────────────────────────────


@pytest.mark.parametrize(
    ("answers", "expected"),
    [
        ({"c1": "yes", "c2": "yes", "r1": "no"}, "pass"),
        ({"c1": "yes", "c2": "no", "r1": "no"}, "fail"),
        ({"c1": "yes", "c2": "yes", "r1": "yes"}, "fail"),
        # Сработавший `reject` важнее «не видно» по обязательным.
        ({"c1": "unclear", "c2": "yes", "r1": "yes"}, "fail"),
        ({"c1": "yes", "c2": "unclear", "r1": "no"}, "unclear"),
        ({"c1": "yes", "c2": "yes", "r1": "unclear"}, "unclear"),
        # Промолчала про `reject` — это «не видно», а не «не нашла».
        ({"c1": "yes", "c2": "yes"}, "unclear"),
    ],
)
def test_verdict_is_computed_from_items(answers: Dict[str, str], expected: str) -> None:
    spec = rubric_review_service.rubric_spec(_CRITERIA_RULES)
    raw = json.dumps({"items": [{"id": k, "met": v, "evidence": "…"} for k, v in answers.items()]})
    parsed = rubric_review_service._parse(raw, spec["items"], source="grading_criteria")
    assert parsed["suggested_verdict"] == expected
    assert parsed["suggested_score"] is None
    assert parsed["source"] == "grading_criteria"


def test_verdict_from_model_is_ignored() -> None:
    spec = rubric_review_service.rubric_spec(_CRITERIA_RULES)
    raw = json.dumps({
        "suggested_verdict": "pass",
        "verdict": "pass",
        "items": [{"id": "c1", "met": "no", "evidence": "нет чтения n"}],
    })
    parsed = rubric_review_service._parse(raw, spec["items"], source="grading_criteria")
    assert parsed["suggested_verdict"] == "fail"


def test_ta_rubric_has_score_but_no_verdict() -> None:
    spec = rubric_review_service.rubric_spec(_TA_RULES)
    raw = json.dumps({"items": [{"id": "c1", "met": "yes"}, {"id": "c2", "met": "yes"}]})
    parsed = rubric_review_service._parse(raw, spec["items"], source="text_rubric")
    assert parsed["suggested_score"] == 3
    assert parsed["suggested_verdict"] is None
    assert all(item["kind"] == "must" for item in parsed["items"])


# ─────────────────────────── Текст для судьи ─────────────────────────────────


def test_answer_is_built_from_both_fields() -> None:
    body = rubric_review_service.pick_answer_for_criteria("25", _PROGRAM)
    assert body is not None
    assert body.startswith("Ответ:\n25")
    assert "Комментарий:\n" + _PROGRAM.strip() in body


def test_attachment_code_is_added_once() -> None:
    # Программа из вложения, которой нет в полях, — добавляется.
    body = rubric_review_service.pick_answer_for_criteria("25", None, _PROGRAM)
    assert "Программа из вложения:\n" + _PROGRAM.strip() in body
    # Та же программа в комментарии — второй раз не идёт.
    body = rubric_review_service.pick_answer_for_criteria("25", _PROGRAM, _PROGRAM)
    assert body.count(_PROGRAM.strip()) == 1


def test_attachment_names_are_told_to_the_judge() -> None:
    """Файлы, которых модель не видит, названы по именам — чтобы «нет» стало «не видно»."""
    body = rubric_review_service.pick_answer_for_criteria(
        _JUDGEMENT, None,
        attachments=[{"filename": "menu.png", "attachment_id": "x"}, "мусор", {"size_bytes": 1}],
    )
    assert body.endswith("Приложены файлы (их содержимое тебе недоступно): menu.png")
    # Без файлов строки нет, и мусор в поле не роняет сборку.
    assert "Приложены файлы" not in rubric_review_service.pick_answer_for_criteria(_JUDGEMENT, None)
    assert rubric_review_service.pick_answer_for_criteria(_JUDGEMENT, None, attachments="oops")


def test_short_answer_is_below_threshold_but_code_is_not() -> None:
    assert rubric_review_service.pick_answer_for_criteria(
        "не знаю", None, min_chars=rubric_review_service.MIN_TEXT_CHARS,
    ) is None
    assert rubric_review_service.pick_answer_for_criteria("не знаю", None, min_chars=0)
    assert rubric_review_service.pick_answer_for_criteria(None, None) is None


async def test_review_uses_own_purpose_and_no_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: Dict[str, Any] = {}

    async def _fake_complete(messages, **kwargs):
        seen.update(kwargs)
        seen["user"] = messages[-1].content
        return _fake_result({"items": [
            {"id": "c1", "met": "yes", "evidence": "n = int(input())"},
            {"id": "c2", "met": "yes", "evidence": "x % 5 == 0"},
            {"id": "r1", "met": "no", "evidence": "кратность проверяется"},
        ], "summary": "всё на месте"})

    monkeypatch.setattr(rubric_review_service, "complete", _fake_complete)
    review = (await rubric_review_service.review_against_rubric(
        "Ответ:\n25", solution_rules=_CRITERIA_RULES, task_stem="Найди максимум",
        purpose=rubric_review_service.CRITERIA_PURPOSE, min_chars=0,
    ))["rubric_review"]
    assert seen["purpose"] == "criteria_review"
    assert "Что НЕ засчитывать" in seen["user"]
    assert review["suggested_verdict"] == "pass"
    assert review["items"][2]["kind"] == "reject"


# ───────────────────────── Пометка при приёме ответа ─────────────────────────


def _service_headers() -> dict[str, str]:
    return {"X-API-Key": next(iter(_settings.valid_api_keys))}


async def _insert_task(s: AsyncSession, course_id: int, *, task_type: str, rules: Optional[dict]) -> int:
    difficulty_id = (await s.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))).scalar()
    return (await s.execute(text(
        "INSERT INTO tasks (task_content, solution_rules, course_id, difficulty_id, external_uid, "
        "max_attempts, max_score) VALUES (CAST(:tc AS jsonb), CAST(:sr AS jsonb), :cid, :did, :uid, 3, 1) "
        "RETURNING id"
    ), {
        "tc": json.dumps({"type": task_type, "stem": "Найди максимум среди кратных 5", "media": []}),
        "sr": json.dumps(rules),
        "cid": course_id, "did": difficulty_id,
        "uid": f"tsk958-{uuid.uuid4().hex[:12]}",
    })).scalar()


@pytest_asyncio.fixture(scope="function")
async def criteria_graph(db):
    """Курс, три задания (критерии без эталона / с эталоном / SA) и записанный ученик.

    Всё — в общей откатываемой транзакции `db`: приложение сидит на том же
    соединении (`_override_app_db`), поэтому и вставки теста видны запросам,
    и записанный приёмом ответа отчёт виден тесту. Отдельный движок здесь не
    годится: он не увидел бы незакоммиченный `task_results`, а его уборка
    встала бы на замке строки `tasks`, который держит FK этого же отчёта.
    """
    ids: dict[str, int] = {}
    ids["course"] = (await db.execute(text(
        "INSERT INTO courses (title, access_level) VALUES ('tsk958 курс', 'self_guided') RETURNING id"
    ))).scalar()
    ids["task_criteria"] = await _insert_task(db, ids["course"], task_type="SA_COM", rules=_CRITERIA_RULES)
    ids["task_sa"] = await _insert_task(db, ids["course"], task_type="SA", rules=_CRITERIA_RULES)
    with_reference = {
        **_CRITERIA_RULES,
        "short_answer": {"normalization": ["trim"], "accepted_answers": [{"value": "25", "score": 1}]},
    }
    ids["task_reference"] = await _insert_task(db, ids["course"], task_type="SA_COM", rules=with_reference)
    ids["task_attachment"] = await _insert_task(
        db, ids["course"], task_type="SA_COM", rules={**_CRITERIA_RULES, "requires_attachment": True},
    )
    ids["student"] = (await db.execute(text(
        "INSERT INTO users (full_name) VALUES ('tsk958 ученик') RETURNING id"
    ))).scalar()
    await db.execute(text(
        "INSERT INTO user_courses (user_id, course_id, is_active) VALUES (:u, :c, true)"
    ), {"u": ids["student"], "c": ids["course"]})
    await db.flush()
    yield ids, db


async def _submit(client, ids: dict, task_key: str, response: dict, *, task_type: str = "SA_COM") -> Any:
    resp = await client.post(
        "/api/v1/attempts",
        json={"user_id": ids["student"], "course_id": ids["course"], "source_system": "test_tsk958"},
        headers=_service_headers(),
    )
    assert resp.status_code == 201, resp.text
    attempt_id = resp.json()["id"]
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=ids["student"], is_service=False)
    try:
        resp = await client.post(
            f"/api/v1/attempts/{attempt_id}/answers",
            json={"items": [{"task_id": ids[task_key], "answer": {"type": task_type, "response": response}}]},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _code_review_of(s: AsyncSession, student_id: int, task_id: int) -> Optional[dict]:
    return (await s.execute(text(
        "SELECT code_review FROM task_results WHERE user_id = :u AND task_id = :t ORDER BY id DESC LIMIT 1"
    ), {"u": student_id, "t": task_id})).scalar()


async def test_judgement_answer_is_queued_as_criteria(client, criteria_graph, criteria_enabled) -> None:
    """Текст-суждение без программы → очередь вида `criteria` со снимком обоих полей."""
    ids, s = criteria_graph
    body = await _submit(client, ids, "task_criteria", {"value": _JUDGEMENT, "comment": "дополню позже"})
    # Ученику отчёт не отдаётся — ни ключа, ни статуса.
    assert "code_review" not in json.dumps(body, ensure_ascii=False)

    review = await _code_review_of(s, ids["student"], ids["task_criteria"])
    assert review is not None
    assert review["status"] == "pending"
    assert review["kind"] == "criteria"
    assert review["code"].startswith("Ответ:\n" + _JUDGEMENT[:20])
    assert "Комментарий:\nдополню позже" in review["code"]


async def test_program_in_comment_keeps_code_review_and_adds_rubric(
    client, criteria_graph, criteria_enabled,
) -> None:
    """Программа найдена → оценка кода как прежде, разбор по критериям — вдобавок."""
    ids, s = criteria_graph
    await _submit(client, ids, "task_criteria", {"value": "25", "comment": _PROGRAM})
    review = await _code_review_of(s, ids["student"], ids["task_criteria"])
    assert review["status"] == "pending"
    assert review["kind"] == "code"
    assert review["rubric"] is True


async def test_switch_off_means_no_criteria_queue(client, criteria_graph, monkeypatch) -> None:
    """Рубильник выключен (умолчание) — короткий ответ в очередь не ставится."""
    ids, s = criteria_graph
    monkeypatch.setitem(settings_store._cabinet, "criteria_review_enabled", False)
    await _submit(client, ids, "task_criteria", {"value": _JUDGEMENT})
    assert await _code_review_of(s, ids["student"], ids["task_criteria"]) is None


async def test_reference_answer_is_not_judged_by_criteria(client, criteria_graph, criteria_enabled) -> None:
    """С эталоном сверяет `checking_service`; второе мнение по критериям не нужно."""
    ids, s = criteria_graph
    await _submit(client, ids, "task_reference", {"value": _JUDGEMENT})
    assert await _code_review_of(s, ids["student"], ids["task_reference"]) is None


async def test_attachment_task_is_not_judged_by_text(client, criteria_graph, criteria_enabled) -> None:
    """Доказательство — в файле, которого модель не видит: дверь `ai_check_policy` закрыта, очереди нет."""
    ids, s = criteria_graph
    await _submit(client, ids, "task_attachment", {"value": _JUDGEMENT})
    assert await _code_review_of(s, ids["student"], ids["task_attachment"]) is None


async def test_short_answer_is_not_queued(client, criteria_graph, criteria_enabled) -> None:
    ids, s = criteria_graph
    await _submit(client, ids, "task_sa", {"value": "не знаю"}, task_type="SA")
    assert await _code_review_of(s, ids["student"], ids["task_sa"]) is None


# ──────────────────────────────── Фоновый тик ────────────────────────────────


async def _seed_pending(db, *, kind: str, rules: Dict[str, Any], response: Dict[str, Any],
                        report: Dict[str, Any]) -> int:
    course_id = (await db.execute(text(
        "INSERT INTO courses (title, access_level) VALUES ('tsk958', 'manual_check') RETURNING id"
    ))).scalar_one()
    task_id = (await db.execute(text(
        "INSERT INTO tasks (external_uid, max_score, task_content, solution_rules, course_id, difficulty_id) "
        "VALUES (:ext, 1, CAST(:c AS jsonb), CAST(:r AS jsonb), :cid, 1) RETURNING id"
    ), {
        "ext": f"tsk958-{random.randint(10**8, 10**10)}",
        "c": json.dumps({"type": "SA_COM", "stem": "Найди максимум среди кратных 5"}),
        "r": json.dumps(rules),
        "cid": course_id,
    })).scalar_one()
    user_id = (await db.execute(text("SELECT id FROM users ORDER BY id LIMIT 1"))).scalar_one()
    now = datetime.now(timezone.utc)
    result_id = (await db.execute(text(
        "INSERT INTO task_results (score, user_id, task_id, submitted_at, count_retry, received_at, "
        " max_score, source_system, answer_json, code_review) "
        "VALUES (0, :u, :t, :now, 0, :now, 1, 'test', CAST(:a AS jsonb), CAST(:cr AS jsonb)) RETURNING id"
    ), {
        "u": user_id, "t": task_id, "now": now,
        "a": json.dumps({"type": "SA_COM", "response": response}),
        "cr": json.dumps({"status": "pending", "kind": kind, **report}),
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


async def test_tick_judges_criteria_work_without_authorship(
    db, db_session_factory, monkeypatch: pytest.MonkeyPatch, criteria_enabled,
) -> None:
    """Вид `criteria`: один вызов модели — разбор; признак авторства не зовётся."""
    result_id = await _seed_pending(
        db, kind="criteria", rules=_CRITERIA_RULES,
        response={"value": _JUDGEMENT}, report={"code": "Ответ:\n" + _JUDGEMENT},
    )
    calls: Dict[str, int] = {"rubric": 0}

    async def _never_authorship(*a, **kw):  # pragma: no cover — вызов = провал
        raise AssertionError("признак авторства для SA_COM не включаем (tsk-646)")

    async def _fake_complete(messages, **kwargs):
        calls["rubric"] += 1
        assert kwargs["purpose"] == "criteria_review"
        return _fake_result({"items": [
            {"id": "c1", "met": "yes", "evidence": "…"},
            {"id": "c2", "met": "no", "evidence": "проверки кратности нет"},
            {"id": "r1", "met": "yes", "evidence": "максимум среди всех"},
        ], "summary": "кратность не проверяется"})

    monkeypatch.setattr(code_review_cron_service.text_authorship, "review_student_text", _never_authorship)
    monkeypatch.setattr(code_review_cron_service, "review_student_code", _never_authorship)
    monkeypatch.setattr(rubric_review_service, "complete", _fake_complete)

    try:
        summary = await code_review_cron_service.code_review_cron_tick(db_session_factory)
        assert summary["reviewed"] >= 1
        review = await _read_review(db, result_id)
        assert review["status"] == "done"
        assert review["kind"] == "criteria"
        assert review["rubric_review"]["suggested_verdict"] == "fail"
        assert "ai_authorship" not in review
        assert calls["rubric"] == 1
        # Зачёт остаётся за человеком: тик вердикта работе не ставит.
        is_correct = (await db.execute(
            text("SELECT is_correct FROM task_results WHERE id = :r"), {"r": result_id},
        )).scalar_one()
        assert is_correct is None
        # Отчёт проходит через объявленную схему — SPW получает типы, не догадки.
        parsed = CodeReviewReport.model_validate(review)
        assert parsed.rubric_review.suggested_verdict == "fail"
        assert parsed.rubric_review.items[2].kind == "reject"
    finally:
        await _cleanup(db, result_id)


async def test_tick_skips_criteria_work_when_switched_off(
    db, db_session_factory, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Выключили после постановки в очередь — модель не зовётся, работа закрывается честно."""
    monkeypatch.setitem(settings_store._cabinet, "criteria_review_enabled", False)
    result_id = await _seed_pending(
        db, kind="criteria", rules=_CRITERIA_RULES,
        response={"value": _JUDGEMENT}, report={"code": "Ответ:\n" + _JUDGEMENT},
    )

    async def _never(*a, **kw):  # pragma: no cover
        raise AssertionError("выключенный разбор за модель не платит")

    monkeypatch.setattr(rubric_review_service, "complete", _never)
    try:
        await code_review_cron_service.code_review_cron_tick(db_session_factory)
        review = await _read_review(db, result_id)
        assert review["status"] == "skipped"
        assert review["reason"] == "disabled"
    finally:
        await _cleanup(db, result_id)


async def test_tick_puts_rubric_next_to_code_verdict(
    db, db_session_factory, monkeypatch: pytest.MonkeyPatch, criteria_enabled,
) -> None:
    """Кодовая работа с пометкой `rubric`: оценка кода и разбор по критериям — оба в отчёте."""
    result_id = await _seed_pending(
        db, kind="code", rules=_CRITERIA_RULES,
        response={"value": "25", "comment": _PROGRAM}, report={"code": _PROGRAM, "rubric": True},
    )

    async def _fake_code_review(code, *, task_stem=None, student_id=None):
        return {"kind": "code", "code_quality": {"score": 8, "notes": []},
                "ai_authorship": {"verdict": "student_likely"}, "model": "test-model"}

    async def _fake_rubric(text_, *, solution_rules, task_stem=None, student_id=None, **kw):
        assert "Ответ:\n25" in text_ and "Комментарий:" in text_
        assert kw.get("purpose") == "criteria_review"
        return {"rubric_review": {"source": "grading_criteria", "items": [
            {"id": "c1", "title": "…", "max_score": None, "kind": "must", "met": "yes", "evidence": "…"},
        ], "suggested_score": None, "max_score": None, "suggested_verdict": "pass", "summary": "ок"}}

    monkeypatch.setattr(code_review_cron_service, "review_student_code", _fake_code_review)
    monkeypatch.setattr(code_review_cron_service.rubric_review_service, "review_against_rubric", _fake_rubric)

    try:
        await code_review_cron_service.code_review_cron_tick(db_session_factory)
        review = await _read_review(db, result_id)
        assert review["status"] == "done"
        assert review["kind"] == "code"
        assert review["code_quality"]["score"] == 8
        assert review["rubric_review"]["suggested_verdict"] == "pass"
    finally:
        await _cleanup(db, result_id)


async def test_rubric_flag_survives_retry_of_code_review(
    db, db_session_factory, monkeypatch: pytest.MonkeyPatch, criteria_enabled,
) -> None:
    """Временный сбой оценки кода: работа остаётся в очереди с пометкой, разбор не оплачен."""
    result_id = await _seed_pending(
        db, kind="code", rules=_CRITERIA_RULES,
        response={"value": "25", "comment": _PROGRAM}, report={"code": _PROGRAM, "rubric": True},
    )

    async def _flaky_code_review(code, *, task_stem=None, student_id=None):
        return {"error": "LLMTimeout", "message": "timeout", "retryable": True}

    async def _never_rubric(*a, **kw):  # pragma: no cover
        raise AssertionError("при повторе разбор по критериям не считается")

    monkeypatch.setattr(code_review_cron_service, "review_student_code", _flaky_code_review)
    monkeypatch.setattr(code_review_cron_service.rubric_review_service, "review_against_rubric", _never_rubric)

    try:
        await code_review_cron_service.code_review_cron_tick(db_session_factory)
        review = await _read_review(db, result_id)
        assert review["status"] == "pending"
        assert review["rubric"] is True
        assert review["attempts"] == 1
    finally:
        await _cleanup(db, result_id)
