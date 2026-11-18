import pytest
from pydantic import ValidationError

from app.schemas.exam_progress import ExamVariantPublish


def variant(items):
    return {
        "external_uid": "ege-2026-training-01",
        "exam_kind": "ege",
        "root_course_uid": "ege-informatika",
        "title": "Тренировочный вариант 1",
        "sequence": 1,
        "published_version": 1,
        "scale": {
            "version": "ege-info-2026",
            "source_url": "https://example.com/official-scale",
            "primary_max": 2,
            "primary_to_result": {"0": 0, "1": 40, "2": 100},
        },
        "items": items,
    }


def test_variant_contract_accepts_complete_snapshot():
    parsed = ExamVariantPublish.model_validate(variant([
        {"position": 1, "exam_number": 1, "task_external_uid": "course:1", "primary_score": 1},
        {"position": 2, "exam_number": 2, "task_external_uid": "course:2", "primary_score": 1},
    ]))
    assert parsed.sequence == 1


@pytest.mark.parametrize("items", [
    [{"position": 2, "exam_number": 1, "task_external_uid": "course:1", "primary_score": 2}],
    [
        {"position": 1, "exam_number": 1, "task_external_uid": "course:1", "primary_score": 1},
        {"position": 2, "exam_number": 1, "task_external_uid": "course:2", "primary_score": 1},
    ],
    [{"position": 1, "exam_number": 1, "task_external_uid": "course:1", "primary_score": 1}],
])
def test_variant_contract_rejects_inconsistent_content(items):
    with pytest.raises(ValidationError):
        ExamVariantPublish.model_validate(variant(items))


def test_composite_number_publish_contract_counts_weight_once():
    payload = variant([
        {"position": 1, "exam_number": 14, "task_external_uid": "course:14a", "primary_score": 1},
        {"position": 2, "exam_number": 14, "task_external_uid": "course:14b", "primary_score": 1},
    ])
    payload["scale"] = {"version": "test", "source_url": "https://example.com/scale", "primary_max": 1, "primary_to_result": {"0": 0, "1": 100}}
    parsed = ExamVariantPublish.model_validate(payload)
    assert [item.exam_number for item in parsed.items] == [14, 14]


def test_pilot_student_allowlist_blocks_other_students(monkeypatch):
    from fastapi import HTTPException
    from app.api.v1.exam_progress import _student, settings
    from app.auth.current_user import CurrentUser

    monkeypatch.setattr(settings, "exam_progress_student_ids", frozenset({142}))
    assert _student(CurrentUser(id=142)) == 142
    with pytest.raises(HTTPException) as denied:
        _student(CurrentUser(id=143))
    assert denied.value.status_code == 403


def test_service_user_cannot_use_student_flow(monkeypatch):
    from fastapi import HTTPException
    from app.api.v1.exam_progress import _student, settings
    from app.auth.current_user import CurrentUser

    monkeypatch.setattr(settings, "exam_progress_student_ids", frozenset())
    with pytest.raises(HTTPException) as denied:
        _student(CurrentUser(id=1, is_service=True))
    assert denied.value.status_code == 403


@pytest.mark.anyio
async def test_human_user_cannot_publish_variant(monkeypatch):
    from fastapi import HTTPException
    from app.api.v1.exam_progress import publish_variant, settings
    from app.auth.current_user import CurrentUser

    monkeypatch.setattr(settings, "exam_progress_enabled", True)
    with pytest.raises(HTTPException) as denied:
        await publish_variant(variant([]), user=CurrentUser(id=142), db=object())
    assert denied.value.status_code == 403
