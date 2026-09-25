"""tsk-1058/1122: генератор названий не пропускает ответ (число или термин) в название."""
import json

from scripts.backfill_task_titles_tsk612 import _match_batch, _valid_title


def test_numeric_answer_in_title_rejected() -> None:
    assert _valid_title("Кратчайший путь между A и E 9 км", "9") is None
    assert _valid_title("Кратчайший путь длиной 10 км", "10") is None


def test_answer_inside_other_number_kept() -> None:
    assert _valid_title("Кратчайший путь по 19 дорогам", "9") == "Кратчайший путь по 19 дорогам"


def test_word_answer_in_title_rejected() -> None:
    # tsk-1122: реальные находки прода — термин-ответ или его словоформа
    assert _valid_title("Носитель информации", "носитель") is None
    assert _valid_title("Определение алгоритма", "алгоритм") is None
    assert _valid_title("Ошибка на этапе DNS", "DNS") is None
    assert _valid_title("Клиентская валидация перед отправкой", "клиентская валидация") is None


def test_word_answer_absent_kept() -> None:
    assert _valid_title("На что записывают сведения", "носитель") == "На что записывают сведения"
    assert _valid_title("«Сервер не найден» без соединения", "DNS") == "«Сервер не найден» без соединения"
    assert _valid_title("Кратчайший путь от А до Е") == "Кратчайший путь от А до Е"


def test_match_batch_uses_item_answer() -> None:
    payload = json.dumps({"titles": [
        {"id": 1, "title": "Кратчайший путь между A и E 9 км"},
        {"id": 2, "title": "Кратчайший путь от А до F"},
    ]})
    items = [{"id": 1, "answer": "9"}, {"id": 2, "answer": "5"}]
    assert _match_batch(payload, items) == {2: "Кратчайший путь от А до F"}
