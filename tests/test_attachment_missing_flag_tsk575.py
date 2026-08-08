"""
Пометка «файл утрачен» в ответе ученика (tsk-575).

Считается на чтении: метаданные (`filename`, `size_bytes`) остаются следом того,
что файл действительно присылали, а флаг самолечащийся — вернётся файл, исчезнет
и пометка. Чистить `answer_json` нельзя: он же доказательство в разборе жалоб и в
аудите гейтов tsk-227/419.

tsk-593: файлы уехали в объектное хранилище, и проверка наличия стала сетевой.
Поэтому пометка разделена на две части: `mark_missing_attachments` — чистая
функция над уже известным набором имеющихся имён, `mark_missing_one` — та же
пометка, но сама ходит в хранилище. В цикле по строкам зовут первую, иначе
страница из двадцати работ дала бы двадцать запросов подряд.
"""
from __future__ import annotations

import pytest

from app.services.attempt_attachments import (
    build_attachment_id,
    collect_attachment_ids,
    mark_missing_attachments,
    mark_missing_one,
)


def test_mark_missing_attachments_flags_only_absent_files():
    attachment_id = build_attachment_id(1, 7, "lost.py")
    answer_json = {
        "type": "SA",
        "response": {
            "value": "готово",
            "meta": {"attachments": [
                {"attachment_id": attachment_id, "filename": "lost.py"},
            ]},
        },
    }
    # Имеющихся файлов нет вовсе — значит вложение утрачено.
    marked = mark_missing_attachments(answer_json, set())
    assert marked["response"]["meta"]["attachments"][0]["missing"] is True
    # Исходный ответ не мутирован — он может быть кэшем строки БД.
    assert "missing" not in answer_json["response"]["meta"]["attachments"][0]


def test_mark_missing_attachments_keeps_present_file_clean():
    attachment_id = build_attachment_id(42, 7, "alive.py")
    answer_json = {
        "response": {"meta": {"attachments": [{"attachment_id": attachment_id}]}}
    }
    marked = mark_missing_attachments(answer_json, {attachment_id})
    assert "missing" not in marked["response"]["meta"]["attachments"][0]


def test_mark_missing_attachments_passes_through_answers_without_files():
    for value in (None, {}, {"response": {}}, {"response": {"meta": {}}}):
        assert mark_missing_attachments(value, set()) == value


def test_collect_ignores_foreign_looking_ids():
    """`attachment_id` приходит из `answer_json` — доверия ему нет.

    Имя не нашего формата в хранилище не ищется вовсе: иначе мусор из тела
    запроса превращался бы в обращения к хранилищу за файлами, которых мы
    никогда не записывали.
    """
    answer_json = {
        "response": {"meta": {"attachments": [
            {"attachment_id": "1_deadbeef_lost.py"},   # uuid не 32 hex — не наше
            {"attachment_id": "../../etc/passwd"},
            {"attachment_id": 12345},
            {"filename": "без id"},
        ]}}
    }
    assert collect_attachment_ids(answer_json) == []
    # И такие вложения всё равно помечаются утраченными: файла за ними нет.
    marked = mark_missing_attachments(answer_json, set())
    assert all(item.get("missing") for item in marked["response"]["meta"]["attachments"])


@pytest.mark.asyncio
async def test_mark_missing_one_reads_storage(tmp_path, monkeypatch):
    """Одиночная пометка сама спрашивает хранилище (режим диска в тестах)."""
    monkeypatch.setenv("ATTEMPT_ATTACHMENTS_UPLOAD_DIR", str(tmp_path))
    attachment_id = build_attachment_id(42, 7, "alive.py")
    (tmp_path / attachment_id).write_bytes(b"print(1)")

    alive = {"response": {"meta": {"attachments": [{"attachment_id": attachment_id}]}}}
    assert "missing" not in (await mark_missing_one(alive))["response"]["meta"]["attachments"][0]

    lost_id = build_attachment_id(42, 7, "lost.py")
    lost = {"response": {"meta": {"attachments": [{"attachment_id": lost_id}]}}}
    assert (await mark_missing_one(lost))["response"]["meta"]["attachments"][0]["missing"] is True
