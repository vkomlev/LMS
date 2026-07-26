"""tsk-420: SA_COM/TBL_COM с manual_review_required=false должны сразу засчитываться
passed при верном авто-проверенном ответе, не дожидаясь checked_at учителя — ту же
роль для SA_COM/TBL_COM/TA раньше играл blanket-список MANUAL_REVIEW_TASK_TYPES,
игнорируя фактическую опциональность проверки (tsk-247)."""

from app.services.me_service import _compute_syllabus_task_status


def _base_row(**overrides):
    row = {
        "last_submitted_at": "2026-07-21T11:29:12Z",
        "last_is_correct": True,
        "last_checked_at": None,
        "last_score": 1,
        "last_max_score": 1,
        "attempts_used": 1,
        "attempts_limit_effective": 3,
        "has_open_attempt": False,
        "progress_status": None,
        "task_type": "SA_COM",
        "manual_review_required": False,
    }
    row.update(overrides)
    return row


def test_auto_graded_sa_com_passes_without_checked_at():
    row = _base_row(manual_review_required=False, last_checked_at=None)
    assert _compute_syllabus_task_status(row) == "passed"


def test_auto_graded_tbl_com_passes_without_checked_at():
    row = _base_row(task_type="TBL_COM", manual_review_required=False, last_checked_at=None)
    assert _compute_syllabus_task_status(row) == "passed"


def test_mandatory_review_sa_com_still_pending_without_checked_at():
    row = _base_row(manual_review_required=True, last_checked_at=None)
    assert _compute_syllabus_task_status(row) == "pending_review"


def test_mandatory_review_sa_com_passes_after_checked_at():
    row = _base_row(manual_review_required=True, last_checked_at="2026-07-22T09:00:00Z")
    assert _compute_syllabus_task_status(row) == "passed"


def test_ta_always_mandatory_regardless_of_flag():
    row = _base_row(task_type="TA", manual_review_required=False, last_checked_at=None)
    assert _compute_syllabus_task_status(row) == "pending_review"

    row_checked = _base_row(task_type="TA", manual_review_required=False, last_checked_at="2026-07-22T09:00:00Z")
    assert _compute_syllabus_task_status(row_checked) == "passed"


def test_auto_types_unaffected():
    for task_type in ("SC", "MC", "SA"):
        row = _base_row(task_type=task_type, manual_review_required=False, last_checked_at=None)
        assert _compute_syllabus_task_status(row) == "passed"
