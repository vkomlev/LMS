"""Черновики критериев и вычитка человеком (tsk-590).

**Зачем.** Поле критериев выкачено чипом tsk-605, и через неделю оно заполнено
у нуля заданий из 279 (проверено на проде 2026-08-20): писать критерии должен
методист, и задача стояла ровно на этом. Черновик модели снимает блокировку,
но вносит новый риск: заготовка живёт в том же поле, что подтверждённые
критерии, и если бы предикат допуска этого не различал, ученик получал бы
незачёт по правилам, которых никто не читал.

Покрывает:
- (а) черновик критериями НЕ считается: `is_usable`, `has_grading_criteria`,
      `criteria_for_judge`, `ai_check_policy.evaluate`, инвентарь пробелов;
- (б) `criteria_state` — три состояния для очереди вычитки, рубрика TA
      считается подтверждённой (её писал человек);
- (в) запись критериев не трогает остальное правило проверки;
- (г) подтверждение — только от названного человека, не от сервисного ключа;
- (д) генератор черновиков: форма промпта, разбор ответа модели, оговорка о
      классе задания ставится КОДОМ (модель на этот вопрос отвечает неверно в
      обе стороны — замер tsk-605 §5 и повторный замер tsk-590);
- (е) очередь вычитки и пакетная загрузка: сводка по всей выборке, пробный
      прогон ничего не пишет.
"""
from __future__ import annotations

import json
import random
import uuid

import pytest
from sqlalchemy import text

from app.auth.current_user import CurrentUser
from app.core.config import Settings
from app.models.users import Users
from app.schemas.solution_rules import GradingCriteria, SolutionRules
from app.services import ai_check_policy, grading_criteria_draft, grading_criteria_service
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session
from app.services.grading_criteria_service import CriteriaUpdate
from app.services.llm.contracts import LLMResult

pytestmark = pytest.mark.asyncio

_settings = Settings()
_TAG = "tsk590"

_MUST = [
    "Названы ровно два формата требований из недостающих",
    "Для каждого формата указан конкретный открытый вопрос",
]


def _headers() -> dict[str, str]:
    api_key = next(iter(_settings.valid_api_keys))
    return {"X-API-Key": api_key}


def _rules(**extra) -> dict:
    base = {"max_score": 10}
    base.update(extra)
    return base


def _criteria(**extra) -> dict:
    base = {"must": list(_MUST), "accept": [], "reject": []}
    base.update(extra)
    return base


def _approved(**extra) -> dict:
    return _criteria(status="approved", reviewed_by=1, **extra)


# ── (а) черновик критериями не считается ────────────────────────────────────


async def test_draft_is_not_criteria_for_any_consumer():
    """Один факт — три потребителя. Разъехаться им нельзя."""
    rules = SolutionRules.model_validate(_rules(grading_criteria=_criteria()))
    assert rules.grading_criteria.status == "draft"
    assert rules.grading_criteria.is_usable() is False
    assert rules.has_grading_criteria() is False
    assert rules.criteria_for_judge() is None


async def test_approved_criteria_work_as_before():
    rules = SolutionRules.model_validate(_rules(grading_criteria=_approved()))
    assert rules.has_grading_criteria() is True
    assert rules.criteria_for_judge()["source"] == "grading_criteria"


async def test_policy_refuses_task_with_draft_only():
    """Заготовка не открывает заданию машинную проверку.

    Это главная защита задачи: без неё задание становилось бы «пригодным» в
    момент записи черновика — то есть до того, как его прочитал человек.
    """
    verdict = ai_check_policy.evaluate(
        "SA_COM", _rules(manual_review_required=True, grading_criteria=_criteria())
    )
    assert (verdict.allowed, verdict.reason) == (False, "no_reference_no_criteria")
    assert verdict.has_criteria is False


async def test_policy_allows_after_approval():
    verdict = ai_check_policy.evaluate(
        "SA_COM", _rules(manual_review_required=True, grading_criteria=_approved())
    )
    assert (verdict.allowed, verdict.has_criteria) == (True, True)


async def test_approved_without_reviewer_is_rejected():
    """Подтверждение без имени подтвердившего не отличить от заготовки."""
    with pytest.raises(ValueError, match="reviewed_by"):
        GradingCriteria.model_validate(_criteria(status="approved"))


# ── (б) три состояния для очереди вычитки ───────────────────────────────────


@pytest.mark.parametrize(
    "rules_kwargs, expected",
    [
        ({}, "none"),
        ({"grading_criteria": _criteria()}, "draft"),
        ({"grading_criteria": _approved()}, "approved"),
        (
            {
                "text_answer": {
                    "auto_check": False,
                    "rubric": [{"id": "c1", "title": "Тест-план содержит все семь элементов", "max_score": 1}],
                }
            },
            "approved",
        ),
    ],
)
async def test_criteria_state(rules_kwargs, expected):
    """Рубрика TA считается вычитанной: её 148 заданий писал методист руками."""
    rules = SolutionRules.model_validate(_rules(**rules_kwargs))
    assert rules.criteria_state() == expected


# ── (в) запись не трогает остальное правило ─────────────────────────────────


async def _make_course(db) -> int:
    course_id = await db.scalar(
        text("INSERT INTO courses (title, access_level) VALUES (:t, 'auto_check') RETURNING id"),
        {"t": f"{_TAG} курс {uuid.uuid4().hex[:8]}"},
    )
    await db.commit()
    return int(course_id)


async def _make_task(db, course_id: int, *, task_type: str = "SA_COM", rules: dict | None = None) -> int:
    content = {
        "type": task_type,
        "title": f"{_TAG} задание",
        "stem": "Перечисли, каких двух форматов требования не хватает, и какой вопрос остаётся открытым.",
    }
    difficulty_id = await db.scalar(text("SELECT id FROM difficulties LIMIT 1"))
    task_id = await db.scalar(
        text(
            "INSERT INTO tasks (course_id, difficulty_id, external_uid, task_content, "
            "  solution_rules, max_score, order_position, is_active) "
            "VALUES (:c, :d, :uid, CAST(:tc AS jsonb), CAST(:sr AS jsonb), 10, 1, true) RETURNING id"
        ),
        {
            "c": course_id,
            "d": difficulty_id,
            "uid": f"{_TAG}-{uuid.uuid4().hex[:10]}",
            "tc": json.dumps(content),
            "sr": json.dumps(rules if rules is not None else _rules(manual_review_required=True)),
        },
    )
    await db.commit()
    return int(task_id)


async def _new_user(db, role: str) -> tuple[int, str]:
    user = Users(
        email=f"{_TAG}-{role}-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None,
        full_name=f"{_TAG}-{role}",
        tg_id=None,
    )
    db.add(user)
    await db.flush()
    await identity_link_service.upsert_identity(db, user.id, "email", user.email)
    token, _, _ = await create_session(db, user_id=user.id)
    await db.execute(
        text(
            "INSERT INTO user_roles (user_id, role_id) "
            "SELECT :u, r.id FROM roles r WHERE r.name = :role ON CONFLICT DO NOTHING"
        ),
        {"u": user.id, "role": role},
    )
    await db.commit()
    return user.id, token


async def test_write_keeps_rest_of_solution_rules(db):
    """Критерии пишутся точечно: эталон, штрафы и режим проверки на месте.

    Общий `bulk_upsert` перезаписывает правило целиком значением из payload —
    заполнять им критерии значило бы терять всё остальное при первой же
    неполной посылке.
    """
    course_id = await _make_course(db)
    task_id = await _make_task(
        db,
        course_id,
        rules=_rules(
            manual_review_required=True,
            short_answer={"accepted_answers": [{"value": "42", "score": 10}], "normalization": ["trim"]},
            penalties={"wrong_answer": 3},
        ),
    )
    result = await grading_criteria_service.apply(
        db, CriteriaUpdate(task_id=task_id, must=list(_MUST)), reviewer_id=None
    )
    assert (result.ok, result.state) == (True, "draft")

    saved = await db.scalar(text("SELECT solution_rules FROM tasks WHERE id = :t"), {"t": task_id})
    assert saved["short_answer"]["accepted_answers"][0]["value"] == "42"
    assert saved["penalties"]["wrong_answer"] == 3
    assert saved["grading_criteria"]["must"] == _MUST
    assert saved["grading_criteria"]["status"] == "draft"


async def test_approve_records_reviewer(db):
    course_id = await _make_course(db)
    task_id = await _make_task(db, course_id)
    await grading_criteria_service.apply(
        db, CriteriaUpdate(task_id=task_id, must=list(_MUST)), reviewer_id=None
    )
    result = await grading_criteria_service.apply(
        db, CriteriaUpdate(task_id=task_id, approve=True), reviewer_id=77
    )
    assert (result.ok, result.state) == (True, "approved")

    saved = await db.scalar(text("SELECT solution_rules FROM tasks WHERE id = :t"), {"t": task_id})
    assert saved["grading_criteria"]["reviewed_by"] == 77
    assert saved["grading_criteria"]["reviewed_at"] is not None
    # Текст не передавали — он должен остаться прежним, а не обнулиться.
    assert saved["grading_criteria"]["must"] == _MUST


async def test_approve_without_reviewer_refused(db):
    course_id = await _make_course(db)
    task_id = await _make_task(db, course_id, rules=_rules(grading_criteria=_criteria()))
    result = await grading_criteria_service.apply(
        db, CriteriaUpdate(task_id=task_id, approve=True), reviewer_id=None
    )
    assert result.ok is False
    assert "человек" in (result.error or "")


async def test_draft_does_not_overwrite_approved(db):
    """Вычитанные критерии стоили методисту работы — заготовка их не заменяет."""
    course_id = await _make_course(db)
    task_id = await _make_task(db, course_id, rules=_rules(grading_criteria=_approved()))
    outcome = await grading_criteria_service.store_draft(
        db,
        task_id=task_id,
        criteria=GradingCriteria.model_validate(_criteria(origin="ai_draft")),
    )
    assert (outcome.ok, outcome.state) == (False, "approved")
    saved = await db.scalar(text("SELECT solution_rules FROM tasks WHERE id = :t"), {"t": task_id})
    assert saved["grading_criteria"]["status"] == "approved"


async def test_unreadable_rules_answered_not_crashed(db):
    """Правка мимо API (прецедент tsk-396) — отказ с внятным текстом, не 500."""
    course_id = await _make_course(db)
    task_id = await _make_task(db, course_id)
    await db.execute(
        text("UPDATE tasks SET solution_rules = CAST(:sr AS jsonb) WHERE id = :t"),
        {"sr": json.dumps({"max_score": -5}), "t": task_id},
    )
    await db.commit()
    result = await grading_criteria_service.apply(
        db, CriteriaUpdate(task_id=task_id, must=list(_MUST)), reviewer_id=None
    )
    assert result.ok is False
    assert "не разбирается" in (result.error or "")


# ── (д) генератор черновиков ────────────────────────────────────────────────


async def test_prompt_names_what_student_actually_submits():
    """Без этой строки модель пишет критерии под воображаемый артефакт.

    Замер 2026-08-20: для заданий «напишите программу» (тип SA_COM, ученик
    сдаёт короткий ответ) модель уверенно требовала в критериях привести
    алгоритм — текст складный, а проверять по нему нельзя.
    """
    messages = grading_criteria_draft.build_messages(
        stem="Напишите программу…", task_type="SA_COM", course_title="Курс", title="Задание"
    )
    user_text = messages[1].content
    assert "короткий ответ одной строкой" in user_text


async def test_clean_stem_strips_markup_and_soft_hyphens():
    """Мягкие переносы стемов ЕГЭ невидимы, но рвут слова внутри промпта."""
    cleaned = grading_criteria_draft.clean_stem(
        "<p>На­пи­ши­те про­грам­му &laquo;тест&raquo;</p>"
    )
    assert "Напишите программу" in cleaned
    assert "<p>" not in cleaned


@pytest.mark.parametrize(
    "raw, expected",
    [
        ('{"must": ["Названы два формата требований"]}', ["Названы два формата требований"]),
        ('```json\n{"must": ["Названы два формата требований"]}\n```', ["Названы два формата требований"]),
    ],
)
async def test_draft_parses_model_answer(monkeypatch, raw, expected):
    """Провайдер иногда оборачивает JSON в блок кода — черновик из-за этого не теряем."""
    await _run_draft_with(monkeypatch, raw)
    result = await _run_draft_with(monkeypatch, raw)
    assert result.criteria.must == expected
    assert (result.criteria.status, result.criteria.origin) == ("draft", "ai_draft")


async def test_draft_drops_meaningless_items(monkeypatch):
    """«ок» — не критерий; остальные пункты при этом остаются полезными."""
    result = await _run_draft_with(
        monkeypatch,
        json.dumps({"must": ["ок", "Названы два формата требований"], "accept": ["да"]}),
    )
    assert result.criteria.must == ["Названы два формата требований"]
    assert result.criteria.accept == []


async def test_draft_refuses_when_model_gives_nothing(monkeypatch):
    with pytest.raises(grading_criteria_draft.DraftError):
        await _run_draft_with(monkeypatch, json.dumps({"must": ["ок"]}))


async def test_draft_warns_about_attachment(monkeypatch):
    """Оговорку о классе задания ставит КОД: модель на этот вопрос отвечает неверно."""
    result = await _run_draft_with(
        monkeypatch,
        json.dumps({"must": ["Названы два формата требований"]}),
        rules=_rules(requires_attachment=True, manual_review_required=True),
    )
    assert "файл" in (result.criteria.draft_warning or "")


async def test_draft_refuses_empty_stem(monkeypatch):
    with pytest.raises(grading_criteria_draft.DraftError, match="условие"):
        await grading_criteria_draft.generate(
            task_content={"type": "SA_COM", "stem": ""}, solution_rules=_rules()
        )


async def _run_draft_with(monkeypatch, raw: str, *, rules: dict | None = None):
    """Прогнать генератор с подставленным ответом модели — без сети и расхода."""

    async def fake_complete(messages, **kwargs):
        return LLMResult(text=raw, model="test/model", tokens_in=10, tokens_out=5)

    monkeypatch.setattr(grading_criteria_draft.llm_client, "complete", fake_complete)
    return await grading_criteria_draft.generate(
        task_content={"type": "SA_COM", "stem": "Условие задания про форматы требований."},
        solution_rules=rules if rules is not None else _rules(manual_review_required=True),
    )


# ── (е) очередь вычитки и пакет ─────────────────────────────────────────────


async def _queue(client, course_id: int, token: str, **params) -> dict:
    query = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"/api/v1/tasks/grading-criteria/queue?course_id={course_id}"
    if query:
        url = f"{url}&{query}"
    resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_queue_shows_full_stem_and_state(db, client):
    """В очереди нужен текст задания целиком: без него вычитать критерии нечем."""
    _, token = await _new_user(db, "methodist")
    course_id = await _make_course(db)
    empty_id = await _make_task(db, course_id)
    draft_id = await _make_task(
        db, course_id, rules=_rules(manual_review_required=True, grading_criteria=_criteria())
    )

    body = await _queue(client, course_id, token)
    assert body["total"] == 2
    assert (body["drafts_total"], body["empty_total"]) == (1, 1)
    states = {item["task_id"]: item["criteria_state"] for item in body["items"]}
    assert states == {empty_id: "none", draft_id: "draft"}
    assert "форматов требования" in body["items"][0]["stem"]


async def test_queue_filters_by_state(db, client):
    _, token = await _new_user(db, "methodist")
    course_id = await _make_course(db)
    await _make_task(db, course_id)
    draft_id = await _make_task(
        db, course_id, rules=_rules(manual_review_required=True, grading_criteria=_criteria())
    )
    body = await _queue(client, course_id, token, state="draft")
    assert [item["task_id"] for item in body["items"]] == [draft_id]
    # Сводка считается по всей выборке, а не по отфильтрованной странице.
    assert body["empty_total"] == 1


async def test_queue_drops_task_after_approval(db, client):
    _, token = await _new_user(db, "methodist")
    course_id = await _make_course(db)
    task_id = await _make_task(
        db, course_id, rules=_rules(manual_review_required=True, grading_criteria=_criteria())
    )
    resp = await client.post(
        f"/api/v1/tasks/{task_id}/grading-criteria",
        headers={"Authorization": f"Bearer {token}"},
        json={"approve": True},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["criteria_state"], body["machine_gradable"]) == ("approved", True)
    assert (await _queue(client, course_id, token))["total"] == 0


async def test_service_key_cannot_approve(db, client):
    """Скрипт не подтверждает то, что сам же сгенерировал."""
    course_id = await _make_course(db)
    task_id = await _make_task(
        db, course_id, rules=_rules(manual_review_required=True, grading_criteria=_criteria())
    )
    resp = await client.post(
        f"/api/v1/tasks/{task_id}/grading-criteria", headers=_headers(), json={"approve": True}
    )
    assert resp.status_code == 403
    saved = await db.scalar(text("SELECT solution_rules FROM tasks WHERE id = :t"), {"t": task_id})
    # Читаем схемой: у заданий, заведённых мимо API, ключа `status` может не
    # быть вовсе — и «нет ключа» обязано означать «черновик», а не «пусто, ну и
    # ладно». Ровно эта форма пустоты уже давала ложные выводы (JSON-null в
    # `turtle_sim`, tsk-605 §9).
    assert SolutionRules.model_validate(saved).criteria_state() == "draft"


async def test_bulk_reports_every_row_and_dry_run_writes_nothing(db, client):
    """Пакет не прерывается на плохой строке и в пробном прогоне ничего не пишет."""
    _, token = await _new_user(db, "methodist")
    course_id = await _make_course(db)
    good_id = await _make_task(db, course_id)
    payload = {
        "dry_run": True,
        "items": [
            {"task_id": good_id, "must": list(_MUST), "approve": True},
            {"task_id": 10**9, "must": list(_MUST)},
        ],
    }
    resp = await client.post(
        "/api/v1/tasks/grading-criteria/bulk",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["applied"], body["rejected"], body["dry_run"]) == (1, 1, True)
    assert body["items"][1]["error"] == "задание не найдено"

    saved = await db.scalar(text("SELECT solution_rules FROM tasks WHERE id = :t"), {"t": good_id})
    assert saved.get("grading_criteria") is None

    payload["dry_run"] = False
    resp = await client.post(
        "/api/v1/tasks/grading-criteria/bulk",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
    )
    assert resp.json()["applied"] == 1
    saved = await db.scalar(text("SELECT solution_rules FROM tasks WHERE id = :t"), {"t": good_id})
    assert saved["grading_criteria"]["status"] == "approved"


async def test_export_returns_csv_with_criteria(db, client):
    _, token = await _new_user(db, "methodist")
    course_id = await _make_course(db)
    await _make_task(
        db, course_id, rules=_rules(manual_review_required=True, grading_criteria=_criteria())
    )
    resp = await client.get(
        f"/api/v1/tasks/grading-criteria/export?course_id={course_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.content.decode("utf-8")
    assert "task_id" in body
    assert _MUST[0] in body


async def test_queue_requires_role(db, client):
    _, token = await _new_user(db, "student")
    course_id = await _make_course(db)
    resp = await client.get(
        f"/api/v1/tasks/grading-criteria/queue?course_id={course_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
