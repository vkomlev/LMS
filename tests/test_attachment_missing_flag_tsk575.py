"""
Пометка «файл утрачен» в ответе ученика (tsk-575).

Считается на чтении: метаданные (`filename`, `size_bytes`) остаются следом того,
что файл действительно присылали, а флаг самолечащийся — вернётся файл, исчезнет
и пометка. Чистить `answer_json` нельзя: он же доказательство в разборе жалоб и в
аудите гейтов tsk-227/419.
"""
from __future__ import annotations

from app.services.attempt_attachments import build_attachment_id, mark_missing_attachments


def test_mark_missing_attachments_flags_only_absent_files():
    answer_json = {
        "type": "SA",
        "response": {
            "value": "готово",
            "meta": {"attachments": [
                {"attachment_id": "1_deadbeef_lost.py", "filename": "lost.py"},
            ]},
        },
    }
    marked = mark_missing_attachments(answer_json)
    assert marked["response"]["meta"]["attachments"][0]["missing"] is True
    # Исходный ответ не мутирован — он может быть кэшем строки БД.
    assert "missing" not in answer_json["response"]["meta"]["attachments"][0]


def test_mark_missing_attachments_keeps_present_file_clean(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.services.attempt_attachments.upload_dir", lambda: tmp_path
    )
    attachment_id = build_attachment_id(42, 7, "alive.py")
    (tmp_path / attachment_id).write_bytes(b"print(1)")
    answer_json = {
        "response": {"meta": {"attachments": [{"attachment_id": attachment_id}]}}
    }
    marked = mark_missing_attachments(answer_json)
    assert "missing" not in marked["response"]["meta"]["attachments"][0]


def test_mark_missing_attachments_passes_through_answers_without_files():
    for value in (None, {}, {"response": {}}, {"response": {"meta": {}}}):
        assert mark_missing_attachments(value) == value
