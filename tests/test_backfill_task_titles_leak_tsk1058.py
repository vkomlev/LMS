"""tsk-1058: генератор названий не пропускает числовой ответ в название."""
import json

from scripts.backfill_task_titles_tsk612 import _match_batch, _valid_title


def test_numeric_answer_in_title_rejected() -> None:
    assert _valid_title("Кратчайший путь между A и E 9 км", "9") is None
    assert _valid_title("Кратчайший путь длиной 10 км", "10") is None


def test_answer_inside_other_number_kept() -> None:
    assert _valid_title("Кратчайший путь по 19 дорогам", "9") == "Кратчайший путь по 19 дорогам"


def test_text_answer_and_no_answer_kept() -> None:
    assert _valid_title("Носитель информации", "носитель") == "Носитель информации"
    assert _valid_title("Кратчайший путь от А до Е") == "Кратчайший путь от А до Е"


def test_match_batch_uses_item_answer() -> None:
    payload = json.dumps({"titles": [
        {"id": 1, "title": "Кратчайший путь между A и E 9 км"},
        {"id": 2, "title": "Кратчайший путь от А до F"},
    ]})
    items = [{"id": 1, "answer": "9"}, {"id": 2, "answer": "5"}]
    assert _match_batch(payload, items) == {2: "Кратчайший путь от А до F"}
