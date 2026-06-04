from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1 import tasks_extra
from app.schemas.tasks import GoogleSheetsImportRequest
from app.services.sheets_parser_service import SheetsParserService
from app.services.tasks_service import TasksService
from app.utils.exceptions import DomainError


def _parse(task_content_json: str | None = None, *, prompt: str = "flat prompt") -> dict[str, Any]:
    row = {
        "external_uid": "TASK-JSON-1",
        "type": "SA",
        "stem": "Question",
        "correct_answer": "42",
        "prompt": prompt,
        "task_content_json": task_content_json or "",
    }
    content, _, _ = SheetsParserService().parse_task_row(row)
    return content.model_dump()


def test_task_content_json_preserves_multi_hints():
    content = _parse('{"hints_video": ["url1", "url2"], "hints_text": ["text1"], "has_hints": true}')

    assert content["hints_video"] == ["url1", "url2"]
    assert content["hints_text"] == ["text1"]
    assert content["has_hints"] is True


def test_task_content_json_preserves_images_and_attached_files():
    content = _parse(
        '{"stem_images": ["png1"], "attached_file_paths": ["f.ods"], "has_attached_file": true}'
    )

    assert content["stem_images"] == ["png1"]
    assert content["attached_file_paths"] == ["f.ods"]
    assert content["has_attached_file"] is True


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("not-json", "task_content_json_invalid"),
        ('["array"]', "task_content_json_not_object"),
    ],
)
def test_task_content_json_rejects_invalid_values(value: str, expected: str):
    with pytest.raises(DomainError, match=expected):
        _parse(value)


def test_task_content_json_absent_keeps_previous_content():
    content = _parse()

    assert content["type"] == "SA"
    assert content["stem"] == "Question"
    assert content["prompt"] == "flat prompt"
    assert "stem_images" not in content


def test_task_content_json_empty_keeps_previous_content():
    assert _parse("") == _parse()


def test_task_content_json_overrides_flat_prompt():
    content = _parse('{"prompt": "json prompt"}')

    assert content["prompt"] == "json prompt"


class _FakeGoogleSheetsService:
    def __init__(self) -> None:
        self.settings = type("Settings", (), {"gsheets_worksheet_name": "Tasks"})()

    def read_sheet(self, *, spreadsheet_id: str, range_name: str) -> list[list[str]]:
        assert spreadsheet_id == "sheet-id"
        assert range_name == "Tasks!A:Z"
        return [
            ["external_uid", "type", "stem", "correct_answer", "task_content_json"],
            ["TASK-JSON-VALID", "SA", "Question", "42", '{"stem_images": ["graph.png"]}'],
            ["TASK-JSON-INVALID", "SA", "Question", "42", "not-json"],
        ]


class _FakeTasksService:
    last_bulk_items: list[dict[str, Any]] | None = None

    async def validate_task_import(self, _db: object, **_kwargs: Any) -> tuple[bool, list[str]]:
        return True, []

    async def bulk_upsert(
        self,
        _db: object,
        items: list[dict[str, Any]],
    ) -> list[tuple[str, str, int]]:
        type(self).last_bulk_items = items
        return [(items[0]["external_uid"], "created", 101)]


@pytest.mark.asyncio
@pytest.mark.parametrize("dry_run", [True, False])
async def test_google_sheets_import_keeps_valid_rows_when_json_is_invalid(monkeypatch, dry_run: bool):
    _FakeTasksService.last_bulk_items = None
    monkeypatch.setattr(tasks_extra, "GoogleSheetsService", _FakeGoogleSheetsService)
    monkeypatch.setattr(tasks_extra, "TasksService", _FakeTasksService)

    response = await tasks_extra.import_from_google_sheets(
        GoogleSheetsImportRequest(
            spreadsheet_url="sheet-id",
            course_id=1,
            difficulty_id=1,
            dry_run=dry_run,
        ),
        db=object(),
    )

    assert response.imported == 1
    assert response.updated == 0
    assert response.total_rows == 2
    assert len(response.errors) == 1
    assert response.errors[0].external_uid == "TASK-JSON-INVALID"
    assert response.errors[0].error.startswith("task_content_json_invalid:")
    if dry_run:
        assert _FakeTasksService.last_bulk_items is None
    else:
        assert _FakeTasksService.last_bulk_items is not None
        assert _FakeTasksService.last_bulk_items[0]["task_content"]["stem_images"] == ["graph.png"]


@pytest.mark.asyncio
async def test_bulk_upsert_persists_task_content_extensions_in_jsonb(db: AsyncSession):
    row = (
        await db.execute(
            text(
                """
                INSERT INTO courses (title, description, access_level, is_required)
                VALUES ('test_task_content_json', 'test', 'self_guided', false)
                RETURNING id
                """
            )
        )
    ).first()
    course_id = int(row.id)
    await db.flush()

    try:
        result = await TasksService().bulk_upsert(
            db,
            [
                {
                    "external_uid": "TASK-CONTENT-JSON-DB",
                    "course_id": course_id,
                    "difficulty_id": 1,
                    "task_content": {
                        "type": "SA",
                        "stem": "Question",
                        "stem_images": ["graph.png"],
                        "attached_file_paths": ["data.xlsx"],
                        "has_attached_file": True,
                    },
                    "solution_rules": {
                        "type": "SA",
                        "accepted_answers": ["42"],
                        "max_score": 1,
                    },
                    "max_score": 1,
                }
            ],
        )

        stored = (
            await db.execute(
                text("SELECT task_content FROM tasks WHERE id = :task_id"),
                {"task_id": result[0][2]},
            )
        ).scalar_one()
        assert stored["stem_images"] == ["graph.png"]
        assert stored["attached_file_paths"] == ["data.xlsx"]
        assert stored["has_attached_file"] is True
    finally:
        await db.execute(
            text("DELETE FROM tasks WHERE external_uid = 'TASK-CONTENT-JSON-DB'")
        )
        await db.execute(
            text("DELETE FROM courses WHERE id = :course_id"),
            {"course_id": course_id},
        )
        await db.commit()
