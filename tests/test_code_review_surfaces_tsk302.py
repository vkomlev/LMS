# tests/test_code_review_surfaces_tsk302.py
"""
tsk-302: машинная оценка доезжает до всех трёх поверхностей преподавателя.

До этого отчёт был виден только на экране проверки одной работы. Оператор
попросил показывать его ещё в ленте активности и в прогрессе ученика, а полный
разбор — в карточке задания.

Разные поверхности получают РАЗНЫЙ объём: в списках значок (балл + повод
присмотреться), в карточке — отчёт целиком. Это не украшательство: лента отдаёт
до сотни событий, и полный JSON там был бы весом ради данных, которые в строку
всё равно не влезут.
"""
from __future__ import annotations

import pytest

from app.schemas.code_review import CodeReviewBadge, build_code_review_badge


# ---------- Сворачивание отчёта в значок ----------

def test_badge_from_full_report() -> None:
    badge = build_code_review_badge({
        "status": "done",
        "language": "Python",
        "code_quality": {"score": 7, "notes": ["a", "b"]},
        "ai_authorship": {"verdict": "student_likely", "reasoning": "..."},
    })
    assert badge == CodeReviewBadge(status="done", score=7, ai_suspected=False, degraded=False)


def test_badge_marks_ai_suspicion() -> None:
    """Значок поднимает флаг только на `ai_likely` — «неясно» поводом не считается."""
    suspected = build_code_review_badge({
        "status": "done",
        "code_quality": {"score": 9},
        "ai_authorship": {"verdict": "ai_likely"},
    })
    assert suspected is not None and suspected.ai_suspected is True

    for verdict in ("ambiguous", "student_likely"):
        badge = build_code_review_badge({
            "status": "done", "code_quality": {"score": 9},
            "ai_authorship": {"verdict": verdict},
        })
        assert badge is not None and badge.ai_suspected is False


def test_badge_takes_lint_score_when_model_was_unavailable() -> None:
    """
    У деградированного отчёта балл лежит в разборе линтера — значок берёт его.

    Иначе при недоступной модели список показывал бы пустоту там, где оценка
    на самом деле есть (тот же класс дефекта, что ревью нашло в панели, Б3).
    """
    badge = build_code_review_badge({
        "status": "done",
        "degraded": True,
        "error": "LLMConfigError",
        "static": {"pylint": {"score": 8.75}},
    })
    assert badge is not None
    assert badge.score == 9, "дробный балл линтера округляется — значку хватит целого"
    assert badge.degraded is True


def test_badge_absent_when_nothing_to_show() -> None:
    """Нет оценки или формат старый — значка нет, а не пустая заглушка."""
    assert build_code_review_badge(None) is None
    assert build_code_review_badge({}) is None
    # Отчёт этапа 0: только pylint/radon, без `status`.
    assert build_code_review_badge({"code_quality": {"pylint": {"score": 8}}}) is None


def test_badge_survives_pending_and_failed() -> None:
    """Промежуточные состояния доезжают как есть — клиент сам решает, рисовать ли."""
    pending = build_code_review_badge({"status": "pending"})
    assert pending is not None and pending.status == "pending" and pending.score is None

    failed = build_code_review_badge({"status": "failed", "error": "LLMConfigError"})
    assert failed is not None and failed.status == "failed"


# ---------- Инвариант видимости на новых поверхностях ----------

def test_new_surfaces_are_staff_only() -> None:
    """
    Оценка добавлена только в схемы, которые видит персонал.

    Проверяем от обратного: ученические схемы ответа на сдачу не должны получить
    поле ни под каким именем. Это то самое требование оператора, ради которого
    фича вообще устроена так, а не иначе.

    ВАЖНО про границы этого теста. Он смотрит на схемы ответа — и этого мало
    там, где схема ОДНА на оба пути. Карточка задания именно такая: и ученик, и
    преподаватель получают ``TaskHistoryAttempt``, поэтому объявленное поле там
    ничего не говорит о видимости. Ту границу сторожит тест на само тело ответа
    эндпоинта — ``test_student_history_never_carries_code_review`` в
    ``test_task_history_tsk349.py``. Ревью 2026-08-07 нашло утечку ровно в этом
    зазоре: здесь было зелено, а вердикт уходил ученику по сети.
    """
    from app.schemas.attempts import AttemptAnswerResult, AttemptAnswersResponse
    from app.schemas.checking import CheckResult

    for schema in (AttemptAnswersResponse, AttemptAnswerResult, CheckResult):
        for field_name in schema.model_fields:
            lowered = field_name.lower()
            assert "code_review" not in lowered and "authorship" not in lowered, (
                f"{schema.__name__}.{field_name} утекает оценку ученику"
            )


def test_staff_surfaces_declare_the_field() -> None:
    """Три поверхности персонала обязаны поле объявлять — иначе оно не доедет."""
    from app.api.v1.teacher_progress import ProgressTreeItem
    from app.schemas.activity_feed import ActivityFeedEvent
    from app.schemas.task_history import TaskHistoryAttempt
    from app.schemas.teacher_next_modes import ReviewClaimItem

    for schema in (ActivityFeedEvent, ProgressTreeItem, TaskHistoryAttempt, ReviewClaimItem):
        assert "code_review" in schema.model_fields, (
            f"{schema.__name__} не отдаёт code_review — поверхность останется пустой"
        )


def test_lists_get_badge_and_card_gets_full_report() -> None:
    """
    Списки получают компактный тип, карточка — сырой отчёт.

    Если списки начнут отдавать полный JSON, лента на сотне событий раздуется;
    если карточка станет отдавать значок — преподаватель потеряет замечания,
    ради которых её и открывают.
    """
    from app.api.v1.teacher_progress import ProgressTreeItem
    from app.schemas.activity_feed import ActivityFeedEvent
    from app.schemas.task_history import TaskHistoryAttempt

    for list_schema in (ActivityFeedEvent, ProgressTreeItem):
        annotation = str(list_schema.model_fields["code_review"].annotation)
        assert "CodeReviewBadge" in annotation, (
            f"{list_schema.__name__} должна отдавать компактный значок, а не весь отчёт"
        )

    card_annotation = str(TaskHistoryAttempt.model_fields["code_review"].annotation)
    assert "CodeReviewReport" in card_annotation, (
        "карточка задания должна отдавать отчёт целиком, и описанной схемой: "
        "«просто словарь» заставлял клиента приводить типы руками"
    )


def test_report_schema_accepts_every_real_shape() -> None:
    """
    Схема отчёта принимает все формы, которые реально лежат в базе.

    Отчёт стал описанной схемой (ради типов на клиенте), и это добавило риск:
    неожиданная форма — уже не «странный JSON на экране», а 500 на карточке
    преподавателя. Форма `static` приходит из обёртки над pylint/radon без
    нормализации, поэтому сторожим её отдельно: сменится формат обёртки —
    падать должен этот тест, а не экран.
    """
    from app.schemas.code_review import CodeReviewReport

    shapes = [
        # Этап 0: только разбор линтера, без status — старые записи.
        {"code_quality": {"pylint": {"score": 8.75}}},
        # Полный отчёт этапа 3.
        {
            "status": "done", "language": "Python", "model": "test-model",
            "code_quality": {"score": 7, "notes": ["магические числа"]},
            "ai_authorship": {"verdict": "student_likely", "reasoning": "сырой стиль"},
            "static": {
                "pylint": {
                    "score": 8.75,
                    "messages": [{"symbol": "invalid-name", "message": "имя x", "line": 1}],
                },
                "radon": {"complexity": [{"name": "main", "complexity": 3}]},
            },
        },
        # Модель недоступна — деградация до этапа 0.
        {"status": "done", "degraded": True, "error": "LLMConfigError",
         "static": {"pylint": {"score": 6.5, "messages": []}}},
        {"status": "failed", "attempts": 3, "error": "LLMMalformed", "message": "…"},
        {"status": "skipped", "reason": "no_code"},
        {"status": "pending", "attempts": 1, "last_error": "LLMTimeout"},
        {"status": "pending", "backfill": True},
    ]
    for raw in shapes:
        model = CodeReviewReport.model_validate(raw)
        restored = model.model_dump(exclude_none=True)
        for key in raw:
            assert key in restored, (
                f"поле {key} потерялось при отдаче — extra=allow должен его сохранять"
            )


def test_real_lint_output_matches_the_declared_shape() -> None:
    """
    То, что пишет наш анализатор, схема принимает без потерь.

    Проверяем не выдуманную структуру, а результат настоящего разбора: связь
    между обёрткой линтера и схемой ответа иначе держится на честном слове.
    """
    from app.schemas.code_review import CodeReviewReport
    from app.services.code_quality_service import analyze_student_code_quality

    static = analyze_student_code_quality("x = 1\nif x == 42:\n    print('да')\n")
    if not static or static.get("error"):
        pytest.skip("линтер недоступен в этом окружении — проверять нечего")

    report = CodeReviewReport.model_validate({"status": "done", "static": static})
    assert report.static is not None
    dumped = report.model_dump(exclude_none=True)["static"]
    assert dumped.keys() >= static.keys(), "разделы разбора линтера не должны теряться"
