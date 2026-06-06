from app.services.me_service import _compute_syllabus_task_status


def test_syllabus_task_status_skipped_without_submission():
    row = {
        "last_submitted_at": None,
        "last_is_correct": None,
        "last_checked_at": None,
        "attempts_used": 0,
        "attempts_limit_effective": 3,
        "has_open_attempt": False,
        "progress_status": "skipped",
    }

    assert _compute_syllabus_task_status(row) == "skipped"
