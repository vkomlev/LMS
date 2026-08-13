"""
Критерии оценивания как поле задания и гейт допуска к машинной проверке (tsk-605).

**Зачем.** Калибровка tsk-590 на 180 живых сдачах
(`reviews/2026-08-08-tsk590-kalibrovka.md`): с эталоном собственные ошибки
лучшей модели 1.2 %, без эталона — 7.6 % у сильной и 19.0 % у дешёвой, потому
что без эталона модель не пересчитывает задачу, а подтверждает предъявленное
учеником число. На проде 416 активных заданий не имеют ни эталона, ни
критериев (замер 2026-08-13: 268 SA_COM + 148 TA), и в автономном треке без
преподавателя их проверять некому и нечем.

Покрывает:
- (а) схема `GradingCriteria` — валидация «осмысленных предпосылок» на входе,
      как у `partial_auto_check` (tsk-396), а не молчаливое выключение;
- (б) предикаты `has_grading_criteria` / `criteria_for_judge` — оба источника
      (новое поле и рубрика TA), все формы пустоты;
- (в) `ai_check_policy.evaluate` — единая дверь допуска по типам заданий;
- (г) `entitlements_service.check_machine_verdict` — вторая половина условия:
      есть ли кому перехватить ошибочный зачёт;
- (д) инвентарь `GET /tasks/grading-gaps` — задание исчезает из списка ровно
      тогда, когда критерии заполнены;
- (е) регресс: сегодняшний оптимистичный зачёт НЕ изменился (прод в режиме
      `guests`, где отказ `denied_task_not_gradable` не применяется).
"""
from __future__ import annotations

import json
import random
import uuid

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.schemas.solution_rules import GradingCriteria, SolutionRules
from app.services import ai_check_policy, entitlements_service

pytestmark = pytest.mark.asyncio

_settings = Settings()
_TAG = "tsk605"

#: Осмысленный набор критериев — по образцу рубрик TA, уже заполненных на проде
#: (472 пункта, средняя длина 60 символов).
_GOOD_CRITERIA = {
    "must": [
        "Программа читает количество чисел, а затем сами числа",
        "Максимум ищется среди чисел, кратных 5, а не среди всех",
    ],
    "accept": ["Любые имена переменных"],
    "reject": ["Округлённый результат вместо точного значения"],
}


def _headers() -> dict[str, str]:
    api_key = next(iter(_settings.valid_api_keys))
    return {"X-API-Key": api_key}


def _rules(**extra) -> dict:
    base = {"max_score": 10}
    base.update(extra)
    return base


# ── (а) схема: предпосылки проверяются на входе ─────────────────────────────


async def test_criteria_reject_empty_must():
    """Блок без обязательных требований — не критерии.

    Пустой `must` прошёл бы гейт допуска и открыл заданию автономный трек,
    не сказав проверяющему ничего. Это ровно та дыра, ради которой поле и
    заведено, поэтому она закрывается на входе, а не на чтении.
    """
    with pytest.raises(ValueError, match="must"):
        GradingCriteria.model_validate({"must": []})


async def test_criteria_reject_meaningless_item():
    """«ок» вместо критерия — 422, а не молча пригодное к проверке задание."""
    with pytest.raises(ValueError, match="короче"):
        GradingCriteria.model_validate({"must": ["ок"]})


async def test_criteria_reject_duplicates_case_insensitive():
    """Повтор пункта — ошибка: дубль раздувает промпт, не добавляя правил."""
    with pytest.raises(ValueError, match="повторяется"):
        GradingCriteria.model_validate(
            {"must": ["Приведены два примера", "приведены  два примера"]}
        )


async def test_criteria_reject_too_many_items():
    """Список длиннее предела не читает ни человек, ни модель."""
    with pytest.raises(ValueError, match="не больше"):
        GradingCriteria.model_validate(
            {"must": [f"Требование номер {i} к ответу ученика" for i in range(25)]}
        )


async def test_criteria_normalize_whitespace_and_notes():
    """Пробелы схлопываются, пустые заметки не сохраняются как пустая строка."""
    criteria = GradingCriteria.model_validate(
        {"must": ["  Приведены   два   примера с органом чувств "], "notes": "   "}
    )
    assert criteria.must == ["Приведены два примера с органом чувств"]
    assert criteria.notes is None


# ── (б) предикаты: оба источника критериев ──────────────────────────────────


async def test_has_grading_criteria_new_field():
    rules = SolutionRules.model_validate(_rules(grading_criteria=_GOOD_CRITERIA))
    assert rules.has_grading_criteria() is True
    assert rules.criteria_for_judge()["source"] == "grading_criteria"


async def test_has_grading_criteria_reads_legacy_text_rubric():
    """148 заданий TA прода уже заполнены — миграция данных не требуется."""
    rules = SolutionRules.model_validate(
        _rules(
            text_answer={
                "auto_check": False,
                "rubric": [
                    {"id": "c1", "title": "Названы 2 команды, которых у прибора нет", "max_score": 2}
                ],
            }
        )
    )
    assert rules.has_grading_criteria() is True
    judge = rules.criteria_for_judge()
    assert judge["source"] == "text_rubric"
    assert judge["must"] == ["Названы 2 команды, которых у прибора нет"]


async def test_has_grading_criteria_false_for_all_empty_forms():
    """Все формы пустоты: блока нет, JSON-null, блок есть но рубрика пуста."""
    for payload in (
        _rules(),
        _rules(grading_criteria=None, text_answer=None),
        _rules(text_answer={"auto_check": False, "rubric": []}),
    ):
        rules = SolutionRules.model_validate(payload)
        assert rules.has_grading_criteria() is False
        assert rules.criteria_for_judge() is None


async def test_criteria_for_judge_prefers_new_field_over_rubric():
    """Источник один и тот же у промпта, экрана и инвентаря — иначе разъедутся."""
    rules = SolutionRules.model_validate(
        _rules(
            grading_criteria=_GOOD_CRITERIA,
            text_answer={"auto_check": False, "rubric": [{"id": "c1", "title": "Старая рубрика задания", "max_score": 1}]},
        )
    )
    judge = rules.criteria_for_judge()
    assert judge["source"] == "grading_criteria"
    assert judge["reject"] == ["Округлённый результат вместо точного значения"]


# ── (в) дверь допуска ───────────────────────────────────────────────────────


async def test_policy_blocks_sa_com_without_reference_and_criteria():
    """268 SA_COM прода: сверять нечем — машине не отдаём."""
    verdict = ai_check_policy.evaluate("SA_COM", _rules(manual_review_required=True))
    assert verdict.allowed is False
    assert verdict.reason == "no_reference_no_criteria"
    assert "нет ни эталона" in verdict.human_reason


async def test_policy_allows_sa_com_with_criteria():
    """Критерии — замена эталона: то же задание допускается."""
    verdict = ai_check_policy.evaluate(
        "SA_COM", _rules(manual_review_required=True, grading_criteria=_GOOD_CRITERIA)
    )
    assert verdict.allowed is True
    assert (verdict.has_reference, verdict.has_criteria) == (False, True)


async def test_policy_allows_sa_com_with_reference():
    verdict = ai_check_policy.evaluate(
        "SA_COM",
        _rules(short_answer={"accepted_answers": [{"value": "42", "score": 10}]}),
    )
    assert verdict.allowed is True
    assert verdict.has_reference is True


async def test_policy_ta_depends_only_on_criteria():
    """У развёрнутого ответа формализуемого эталона нет по определению."""
    assert ai_check_policy.evaluate("TA", _rules()).reason == "no_reference_no_criteria"
    with_rubric = _rules(
        text_answer={"auto_check": False, "rubric": [{"id": "c1", "title": "Есть вывод о формальности исполнителя", "max_score": 3}]}
    )
    assert ai_check_policy.evaluate("TA", with_rubric).allowed is True


async def test_policy_options_types_use_correct_options():
    """У SC/MC эталон живёт в `correct_options`, а не в `short_answer`."""
    assert ai_check_policy.evaluate("SC", _rules(correct_options=["A"])).allowed is True
    assert ai_check_policy.evaluate("SC", _rules()).reason == "no_reference_no_criteria"


async def test_policy_blocks_attachment_even_with_reference():
    """Доказательство лежит в файле, которого модель не видит.

    В разведке tsk-590 модели по таким работам честно отказывались выносить
    вердикт. Причина отдельная: критерии её не снимают, решение продуктовое
    (tsk-301), и в инвентаре методиста такие задания не должны выглядеть как
    «допиши критерии».
    """
    verdict = ai_check_policy.evaluate(
        "SA_COM",
        _rules(
            requires_attachment=True,
            short_answer={"accepted_answers": [{"value": "42", "score": 10}]},
        ),
    )
    assert verdict.allowed is False
    assert verdict.reason == "attachment_not_readable"


async def test_policy_unreadable_rules_are_refused_not_crashed():
    """Правка `solution_rules` прямо в БД мимо API валидатор обходит (tsk-396)."""
    assert ai_check_policy.evaluate("SA_COM", {"max_score": 0}).reason == "invalid_rules"
    assert ai_check_policy.evaluate("SA_COM", None).reason == "invalid_rules"
    assert ai_check_policy.evaluate(None, _rules()).reason == "invalid_rules"


async def test_policy_quiz_needs_no_verdict():
    """Квизы со шкалами «верно/неверно» не выносят — отказывать не в чем."""
    quiz = _rules(quiz={"scales": ["python"], "mode": "single"})
    assert ai_check_policy.evaluate("SC_Qw", quiz).allowed is True


# ── (г) вторая половина: есть ли кому перехватить ───────────────────────────


async def _make_plan(db, *, teacher_escalation: bool) -> int:
    code = f"{_TAG}_{uuid.uuid4().hex[:8]}"
    return int(
        (
            await db.execute(
                text(
                    "INSERT INTO subscription_plan (code, name, teacher_escalation) "
                    "VALUES (:c, :c, :te) RETURNING id"
                ),
                {"c": code, "te": teacher_escalation},
            )
        ).scalar()
    )


async def _make_student(db, *, plan_id: int | None = None) -> int:
    sid = int(
        (
            await db.execute(
                text(
                    "INSERT INTO users (email, full_name) "
                    "VALUES (:e, :n) RETURNING id"
                ),
                {"e": f"{_TAG}_{uuid.uuid4().hex[:8]}@example.com", "n": f"{_TAG} ученик"},
            )
        ).scalar()
    )
    if plan_id is not None:
        await db.execute(
            text(
                "INSERT INTO student_subscription (student_id, plan_id) VALUES (:s, :p)"
            ),
            {"s": sid, "p": plan_id},
        )
    await db.commit()
    return sid


async def test_machine_verdict_allowed_when_task_is_gradable(db):
    """Годное задание допускается независимо от тарифа."""
    student_id = await _make_student(db)
    decision = await entitlements_service.check_machine_verdict(
        db,
        student_id=student_id,
        task_type="SA_COM",
        solution_rules=_rules(grading_criteria=_GOOD_CRITERIA),
    )
    assert (decision.allowed, decision.outcome) == (True, "allowed")


async def test_machine_verdict_allowed_when_teacher_can_intercept(db):
    """Пока в тарифе есть преподаватель — сегодняшний порядок не ломаем."""
    plan_id = await _make_plan(db, teacher_escalation=True)
    student_id = await _make_student(db, plan_id=plan_id)
    decision = await entitlements_service.check_machine_verdict(
        db, student_id=student_id, task_type="SA_COM", solution_rules=_rules()
    )
    assert (decision.allowed, decision.outcome) == (True, "allowed")


async def test_machine_verdict_denied_for_autonomous_student(db):
    """Без преподавателя ошибочный зачёт перехватить некому — машине нельзя."""
    plan_id = await _make_plan(db, teacher_escalation=False)
    student_id = await _make_student(db, plan_id=plan_id)
    decision = await entitlements_service.check_machine_verdict(
        db, student_id=student_id, task_type="SA_COM", solution_rules=_rules()
    )
    assert decision.allowed is False
    assert decision.outcome == "denied_task_not_gradable"
    assert "преподаватель" in (decision.upgrade_hint or "")


async def test_machine_verdict_denied_when_student_unknown(db):
    """Неизвестность — на стороне безопасности, а не разрешения."""
    decision = await entitlements_service.check_machine_verdict(
        db, student_id=None, task_type="TA", solution_rules=_rules()
    )
    assert decision.outcome == "denied_task_not_gradable"


async def test_machine_verdict_not_applied_in_current_gate_mode(db):
    """Регресс: сегодня отказ НЕ применяется — прод стоит в режиме `guests`.

    Предохранитель включается вместе с гейтом подписки (tsk-301). Тест
    фиксирует, что установка поля не изменила поведение задним числом: иначе
    ученики demo/alumni перестали бы получать зачёт по 416 заданиям молча.
    """
    plan_id = await _make_plan(db, teacher_escalation=False)
    student_id = await _make_student(db, plan_id=plan_id)
    decision = await entitlements_service.check_machine_verdict(
        db, student_id=student_id, task_type="SA_COM", solution_rules=_rules()
    )
    assert decision.allowed is False
    for mode in ("off", "shadow", "guests"):
        entitlements_service.settings.subscription_gate_mode = mode
        assert entitlements_service.should_block(decision) is False, mode
    entitlements_service.settings.subscription_gate_mode = "on"
    assert entitlements_service.should_block(decision) is True
    entitlements_service.settings.subscription_gate_mode = _settings.subscription_gate_mode


# ── (д) инвентарь методиста ─────────────────────────────────────────────────


async def _make_course(db) -> int:
    cid = int(
        (
            await db.execute(
                text(
                    "INSERT INTO courses (title, access_level) "
                    "VALUES (:t, 'auto_check') RETURNING id"
                ),
                {"t": f"{_TAG} курс {uuid.uuid4().hex[:6]}"},
            )
        ).scalar()
    )
    await db.commit()
    return cid


async def _make_task(db, course_id: int, *, task_type: str, rules: dict) -> int:
    difficulty_id = (await db.execute(text("SELECT id FROM difficulties LIMIT 1"))).scalar()
    tid = int(
        (
            await db.execute(
                text(
                    "INSERT INTO tasks (course_id, difficulty_id, task_content, "
                    "  solution_rules, external_uid, max_score, order_position) "
                    "VALUES (:c, :d, CAST(:tc AS jsonb), CAST(:sr AS jsonb), :uid, 10, 1) "
                    "RETURNING id"
                ),
                {
                    "c": course_id,
                    "d": difficulty_id,
                    "tc": json.dumps(
                        {"type": task_type, "stem": "<b>Задание</b> tsk-605: напиши про­грамму."}
                    ),
                    "sr": json.dumps(rules),
                    "uid": f"{_TAG}-{random.randint(10**8, 10**10)}",
                },
            )
        ).scalar()
    )
    await db.commit()
    return tid


async def _gaps(client, course_id: int) -> dict:
    resp = await client.get(
        f"/api/v1/tasks/grading-gaps?course_id={course_id}", headers=_headers()
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_grading_gaps_lists_task_without_criteria(db, client):
    """Экран показывает ровно то, что применяет гейт."""
    course_id = await _make_course(db)
    task_id = await _make_task(
        db, course_id, task_type="SA_COM", rules=_rules(manual_review_required=True)
    )
    await _make_task(
        db,
        course_id,
        task_type="SA_COM",
        rules=_rules(short_answer={"accepted_answers": [{"value": "42", "score": 10}]}),
    )

    body = await _gaps(client, course_id)
    assert body["tasks_total"] == 1
    assert body["courses_total"] == 1
    assert body["by_type"] == {"SA_COM": 1}
    assert [item["task_id"] for item in body["items"]] == [task_id]
    preview = body["items"][0]["stem_preview"]
    assert "<b>" not in preview and "­" not in preview
    assert body["by_course"][0]["tasks"] == 1


async def test_grading_gaps_task_disappears_after_criteria_filled(db, client):
    """Заполнил критерии — задание ушло из списка. Без этого поле мертво."""
    course_id = await _make_course(db)
    task_id = await _make_task(
        db, course_id, task_type="SA_COM", rules=_rules(manual_review_required=True)
    )
    assert (await _gaps(client, course_id))["tasks_total"] == 1

    await db.execute(
        text("UPDATE tasks SET solution_rules = CAST(:sr AS jsonb) WHERE id = :t"),
        {
            "sr": json.dumps(_rules(manual_review_required=True, grading_criteria=_GOOD_CRITERIA)),
            "t": task_id,
        },
    )
    await db.commit()
    assert (await _gaps(client, course_id))["tasks_total"] == 0


async def test_grading_gaps_sees_task_saved_through_api(db, client):
    """Задание, правленное через API, не должно пропадать из инвентаря.

    `SolutionRules.model_dump()` пишет незаполненные необязательные блоки явным
    JSON-null, и `solution_rules->'turtle_sim' IS NULL` на таком задании ЛОЖНО:
    в jsonb это JSON-null, а не SQL NULL. Прод 2026-08-13: 363 активных задания
    несут `turtle_sim: null` (настоящих 10), и отбор терял 9 заданий молча —
    экран показывал «пробелов меньше», чем есть.
    """
    course_id = await _make_course(db)
    task_id = await _make_task(
        db, course_id, task_type="SA_COM", rules=_rules(manual_review_required=True)
    )
    # Полный набор ключей — ровно то, что кладёт в базу правка через API.
    await db.execute(
        text("UPDATE tasks SET solution_rules = CAST(:sr AS jsonb) WHERE id = :t"),
        {
            "sr": json.dumps(
                SolutionRules.model_validate(_rules(manual_review_required=True)).model_dump()
            ),
            "t": task_id,
        },
    )
    await db.commit()

    stored = (
        await db.execute(
            text("SELECT jsonb_typeof(solution_rules->'turtle_sim') FROM tasks WHERE id = :t"),
            {"t": task_id},
        )
    ).scalar()
    assert stored == "null", "предпосылка теста: ключ есть и равен JSON-null"

    body = await _gaps(client, course_id)
    assert [item["task_id"] for item in body["items"]] == [task_id]


async def test_grading_gaps_still_skips_real_turtle_sim(db, client):
    """Настоящий `turtle_sim` — это эталон (трасса), такое задание не пробел."""
    course_id = await _make_course(db)
    await _make_task(
        db,
        course_id,
        task_type="SA_COM",
        rules=_rules(
            manual_review_required=True,
            turtle_sim={
                "expected_trace": {
                    "segments": [],
                    "final_state": {"position": [0, 0], "heading": 0.0, "pen_down": True},
                }
            },
        ),
    )
    assert (await _gaps(client, course_id))["tasks_total"] == 0


async def test_grading_gaps_marks_attachment_tasks(db, client):
    """Задание с файлом видно отдельно: критерии его не закроют."""
    course_id = await _make_course(db)
    await _make_task(
        db,
        course_id,
        task_type="SA_COM",
        rules=_rules(manual_review_required=True, requires_attachment=True),
    )
    body = await _gaps(client, course_id)
    assert body["tasks_total"] == 1
    assert body["attachment_blocked"] == 1
    assert body["items"][0]["requires_attachment"] is True


async def test_grading_gaps_summary_counts_whole_selection_not_page(db, client):
    """Сводка считается по всей выборке: постфильтр поверх пагинации терял строки."""
    course_id = await _make_course(db)
    for _ in range(3):
        await _make_task(
            db, course_id, task_type="SA_COM", rules=_rules(manual_review_required=True)
        )
    resp = await client.get(
        f"/api/v1/tasks/grading-gaps?course_id={course_id}&limit=1", headers=_headers()
    )
    body = resp.json()
    assert body["tasks_total"] == 3
    assert len(body["items"]) == 1


# ── (е) критерии сохраняются через правку методистом ────────────────────────


async def test_patch_task_saves_grading_criteria(db, client):
    """Редактор методиста пишет критерии в то же поле, что читает гейт."""
    course_id = await _make_course(db)
    task_id = await _make_task(
        db, course_id, task_type="SA_COM", rules=_rules(manual_review_required=True)
    )
    resp = await client.patch(
        f"/api/v1/tasks/{task_id}",
        headers=_headers(),
        json={"solution_rules": _rules(manual_review_required=True, grading_criteria=_GOOD_CRITERIA)},
    )
    assert resp.status_code == 200, resp.text
    saved = resp.json()["solution_rules"]["grading_criteria"]
    assert saved["must"] == _GOOD_CRITERIA["must"]
    assert (await _gaps(client, course_id))["tasks_total"] == 0


async def test_patch_task_rejects_meaningless_criteria(db, client):
    """422 при заведении, а не тихо непригодное задание в автономном треке."""
    course_id = await _make_course(db)
    task_id = await _make_task(
        db, course_id, task_type="SA_COM", rules=_rules(manual_review_required=True)
    )
    resp = await client.patch(
        f"/api/v1/tasks/{task_id}",
        headers=_headers(),
        json={"solution_rules": _rules(manual_review_required=True, grading_criteria={"must": ["ок"]})},
    )
    assert resp.status_code == 422, resp.text
